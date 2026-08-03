"""Configuration: schema, persistence, legacy migration and env overrides.

The config file is user-editable from the settings page, so it is treated as
data rather than as startup validation: a missing or half-finished config is a
normal state that puts the app into "needs setup" mode instead of exiting.

Precedence, lowest to highest: dataclass defaults -> config.json -> env vars.
Anything set by an env var is reported as locked so the settings page can grey
it out rather than silently failing to save it.
"""

import copy
import json
import os
import shutil
import threading
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from logging_setup import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = 2

KOMETA_MAPPING_URL = (
    "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/master/anime_ids.json"
)

PLACEHOLDER_TOKENS = ("CHANGE_ME", "YOUR_SHOKO_API_KEY", "YOUR_SONARR_API_KEY")

VALID_FORMATS = ["TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA"]
VALID_SORTS = ["POPULARITY_DESC", "SCORE_DESC", "TRENDING_DESC", "FAVOURITES_DESC"]


def _is_placeholder(value):
    return any(token in str(value) for token in PLACEHOLDER_TOKENS)


# ==========================
# Schema
# ==========================

@dataclass
class ShokoSettings:
    url: str = ""
    api_key: str = ""


@dataclass
class SonarrSettings:
    url: str = ""
    api_key: str = ""
    enabled: bool = True

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.url
            and self.api_key
            and not _is_placeholder(self.url)
            and not _is_placeholder(self.api_key)
        )


@dataclass
class AniListSettings:
    url: str = "https://graphql.anilist.co"
    max_results: int = 1000
    min_popularity: int = 50000
    cache_max_age_hours: int = 24
    page_size: int = 50
    request_delay_ms: int = 500
    formats: List[str] = field(default_factory=lambda: ["TV"])
    sort: str = "POPULARITY_DESC"


@dataclass
class MappingSettings:
    path: str = ""
    auto_download: bool = True
    url: str = KOMETA_MAPPING_URL
    max_age_days: int = 7


@dataclass
class ScheduleSettings:
    cron: str = "0 4 * * *"
    run_on_start: bool = True


@dataclass
class UISettings:
    tiers: List[int] = field(default_factory=lambda: [100, 250, 500, 1000])
    history_points: int = 60
    season_limit: int = 20


@dataclass
class NetworkSettings:
    max_retries: int = 4
    initial_backoff_seconds: int = 2
    timeout_seconds: int = 60


@dataclass
class Settings:
    """Everything the user can edit. Serialised verbatim to config.json."""

    version: int = SCHEMA_VERSION
    shoko: ShokoSettings = field(default_factory=ShokoSettings)
    sonarr: SonarrSettings = field(default_factory=SonarrSettings)
    anilist: AniListSettings = field(default_factory=AniListSettings)
    mappings: MappingSettings = field(default_factory=MappingSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    ui: UISettings = field(default_factory=UISettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)

    @property
    def shoko_configured(self) -> bool:
        return bool(self.shoko.url and not _is_placeholder(self.shoko.url))

    @property
    def is_configured(self) -> bool:
        return self.shoko_configured

    def to_dict(self) -> dict:
        return asdict(self)

    def redacted(self) -> dict:
        """Same shape, with secrets masked - safe to hand to the browser."""
        data = self.to_dict()
        data["shoko"]["api_key"] = mask_secret(self.shoko.api_key)
        data["sonarr"]["api_key"] = mask_secret(self.sonarr.api_key)
        return data


def mask_secret(value):
    """Mask a key for display. Empty stays empty so the UI can show 'not set'."""
    if not value:
        return ""
    tail = value[-4:] if len(value) > 4 else ""
    return f"{'*' * 8}{tail}"


# ==========================
# Runtime (container-level, env only)
# ==========================

@dataclass
class Runtime:
    """Deployment-level knobs. Not user-editable from the web UI."""

    config_dir: str = "/config"
    output_dir: str = "/output"
    config_file: str = ""
    cache_file: str = ""
    results_file: str = ""
    database_file: str = ""

    serve_web: bool = True
    web_port: int = 8080
    web_host: str = "0.0.0.0"

    run_once: bool = False


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("Ignoring non-numeric %s=%r, using %s", name, raw, default)
        return default


def load_runtime(argv=None) -> Runtime:
    argv = argv if argv is not None else []

    rt = Runtime()
    rt.config_dir = os.environ.get("CONFIG_DIR", rt.config_dir)
    rt.output_dir = os.environ.get("OUTPUT_DIR", rt.output_dir)
    rt.config_file = os.environ.get(
        "CONFIG_FILE", os.path.join(rt.config_dir, "config.json")
    )
    rt.cache_file = os.environ.get(
        "CACHE_FILE", os.path.join(rt.config_dir, "anilist_cache.json")
    )
    rt.results_file = os.environ.get(
        "RESULTS_FILE", os.path.join(rt.config_dir, "results.json")
    )
    rt.database_file = os.environ.get(
        "DATABASE_FILE", os.path.join(rt.config_dir, "history.db")
    )

    rt.serve_web = _env_bool("SERVE_WEB", rt.serve_web)
    rt.web_port = _env_int("WEB_PORT", rt.web_port)
    rt.web_host = os.environ.get("WEB_HOST", rt.web_host)

    rt.run_once = "--once" in argv or _env_bool("RUN_ONCE", False)

    return rt


# ==========================
# Legacy migration
# ==========================

LEGACY_KEY_MAP = {
    "ShokoURL": ("shoko", "url"),
    "APIKey": ("shoko", "api_key"),
    "SonarrURL": ("sonarr", "url"),
    "SonarrAPIKey": ("sonarr", "api_key"),
    "MappingFile": ("mappings", "path"),
    "MaxResults": ("anilist", "max_results"),
    "MinPopularity": ("anilist", "min_popularity"),
    "CacheMaxAgeHours": ("anilist", "cache_max_age_hours"),
    "CronSchedule": ("schedule", "cron"),
}


def is_legacy(raw: dict) -> bool:
    """A v1 file has no version marker but does have the old PascalCase keys."""
    if raw.get("version"):
        return False
    return any(key in raw for key in LEGACY_KEY_MAP)


def migrate_legacy(raw: dict) -> dict:
    """Translate a v1 config.json into the v2 nested shape."""
    migrated = {"version": SCHEMA_VERSION}

    for legacy_key, (section, field_name) in LEGACY_KEY_MAP.items():
        if legacy_key not in raw:
            continue
        value = raw[legacy_key]
        if value is None or value == "":
            continue
        migrated.setdefault(section, {})[field_name] = value

    # A v1 file carried Sonarr keys only when Sonarr was actually wanted.
    sonarr = migrated.get("sonarr", {})
    migrated.setdefault("sonarr", {})["enabled"] = bool(
        sonarr.get("url") and not _is_placeholder(sonarr.get("url", ""))
    )

    log.info("Migrated legacy config.json to schema v%s", SCHEMA_VERSION)
    return migrated


# ==========================
# Load / save
# ==========================

def _coerce(value, template):
    """Best-effort coercion of a loaded value to the default's type."""
    if isinstance(template, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(template, int) and not isinstance(template, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return template
    if isinstance(template, list):
        return list(value) if isinstance(value, list) else template
    if isinstance(template, str):
        return "" if value is None else str(value)
    return value


def _apply_dict(target, data):
    """Copy known keys from a plain dict onto a nested settings dataclass."""
    if not isinstance(data, dict):
        return

    for key, value in data.items():
        if not hasattr(target, key):
            continue
        current = getattr(target, key)
        if hasattr(current, "__dataclass_fields__"):
            _apply_dict(current, value)
        else:
            setattr(target, key, _coerce(value, current))


def settings_from_dict(data: dict) -> Settings:
    settings = Settings()
    if is_legacy(data):
        data = migrate_legacy(data)
    _apply_dict(settings, data)
    settings.version = SCHEMA_VERSION
    return settings


ENV_OVERRIDES = {
    "MAX_RESULTS": ("anilist", "max_results", int),
    "MIN_POPULARITY": ("anilist", "min_popularity", int),
    "CACHE_MAX_AGE_HOURS": ("anilist", "cache_max_age_hours", int),
    "ANILIST_URL": ("anilist", "url", str),
    "CRON_SCHEDULE": ("schedule", "cron", str),
    "RUN_ON_START": ("schedule", "run_on_start", bool),
    "SHOKO_URL": ("shoko", "url", str),
    "SHOKO_API_KEY": ("shoko", "api_key", str),
    "SONARR_URL": ("sonarr", "url", str),
    "SONARR_API_KEY": ("sonarr", "api_key", str),
    "MAPPING_FILE": ("mappings", "path", str),
}


def apply_env_overrides(settings: Settings):
    """Apply env vars over the file values. Returns the locked dotted paths."""
    locked = []

    for env_name, (section, field_name, kind) in ENV_OVERRIDES.items():
        if env_name not in os.environ:
            continue

        raw = os.environ[env_name]
        target = getattr(settings, section)

        if kind is int:
            value = _env_int(env_name, getattr(target, field_name))
        elif kind is bool:
            value = _env_bool(env_name, getattr(target, field_name))
        else:
            value = raw.strip()

        setattr(target, field_name, value)
        locked.append(f"{section}.{field_name}")

    if locked:
        log.info("Settings locked by environment: %s", ", ".join(sorted(locked)))

    return locked


class ConfigStore:
    """Thread-safe holder for the live settings, backed by config.json."""

    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self._lock = threading.RLock()
        self._settings = Settings()
        self._env_locked: List[str] = []

    # -- access --

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    @property
    def env_locked(self) -> List[str]:
        return list(self._env_locked)

    # -- lifecycle --

    def load(self) -> Settings:
        path = self.runtime.config_file
        raw: Dict = {}

        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                log.error("Could not read %s (%s) - starting from defaults", path, exc)
                raw = {}
        else:
            log.warning("No config file at %s - starting unconfigured", path)

        needs_migration = is_legacy(raw)
        if needs_migration:
            backup = path + ".v1.bak"
            try:
                shutil.copy2(path, backup)
                log.info("Backed up the pre-migration config to %s", backup)
            except OSError as exc:
                log.warning("Could not back up the old config: %s", exc)

        settings = settings_from_dict(raw)

        # Default the mapping path into the config dir if it was never set.
        if not settings.mappings.path:
            settings.mappings.path = os.path.join(
                self.runtime.config_dir, "anime_ids.json"
            )

        self._env_locked = apply_env_overrides(settings)

        with self._lock:
            self._settings = settings

        if needs_migration or not os.path.exists(path):
            self.save(settings)

        return settings

    def save(self, settings: Optional[Settings] = None) -> Settings:
        """Persist settings atomically so a crash can't truncate the file."""
        with self._lock:
            if settings is not None:
                self._settings = settings
            payload = self._settings.to_dict()

        path = self.runtime.config_file
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, path)

        log.info("Configuration saved to %s", path)
        return self.settings

    def update(self, incoming: dict) -> Settings:
        """Merge a partial dict from the settings page into the live settings.

        Env-locked fields are ignored, and a masked secret means "unchanged"
        so the UI never has to round-trip the real key.
        """
        with self._lock:
            candidate = copy.deepcopy(self._settings)

            cleaned = copy.deepcopy(incoming) if isinstance(incoming, dict) else {}
            self._strip_unchanged_secrets(cleaned, candidate)
            self._strip_locked(cleaned)

            _apply_dict(candidate, cleaned)
            validate(candidate)

            self._settings = candidate

        # Env still wins over anything the user just typed.
        apply_env_overrides(self._settings)
        return self.save()

    def _strip_unchanged_secrets(self, incoming, current):
        for section, field_name in (("shoko", "api_key"), ("sonarr", "api_key")):
            block = incoming.get(section)
            if not isinstance(block, dict) or field_name not in block:
                continue
            submitted = block.get(field_name)
            if submitted is None or submitted == "" or str(submitted).startswith("****"):
                block.pop(field_name, None)

    def _strip_locked(self, incoming):
        for dotted in self._env_locked:
            section, field_name = dotted.split(".", 1)
            block = incoming.get(section)
            if isinstance(block, dict):
                block.pop(field_name, None)


class ValidationError(ValueError):
    pass


def validate(settings: Settings):
    """Guard the values that would otherwise fail deep inside a run."""
    from croniter import croniter

    if settings.shoko.url and not settings.shoko.url.startswith(("http://", "https://")):
        raise ValidationError("Shoko URL must start with http:// or https://")

    if settings.sonarr.url and not settings.sonarr.url.startswith(("http://", "https://")):
        raise ValidationError("Sonarr URL must start with http:// or https://")

    if not croniter.is_valid(settings.schedule.cron):
        raise ValidationError(f"'{settings.schedule.cron}' is not a valid cron expression")

    if settings.anilist.max_results < 1:
        raise ValidationError("Max results must be at least 1")

    if settings.anilist.min_popularity < 0:
        raise ValidationError("Minimum popularity cannot be negative")

    if not 1 <= settings.anilist.page_size <= 50:
        raise ValidationError("AniList page size must be between 1 and 50")

    bad_formats = [f for f in settings.anilist.formats if f not in VALID_FORMATS]
    if bad_formats:
        raise ValidationError(f"Unknown AniList format(s): {', '.join(bad_formats)}")

    if not settings.anilist.formats:
        raise ValidationError("Select at least one AniList format")

    if settings.anilist.sort not in VALID_SORTS:
        raise ValidationError(f"Unknown sort order: {settings.anilist.sort}")

    # AniList caps perPage at 50, and the seasons page asks for one page.
    if not 1 <= settings.ui.season_limit <= 50:
        raise ValidationError("Shows per season must be between 1 and 50")

    tiers = [t for t in settings.ui.tiers if isinstance(t, int) and t > 0]
    if not tiers:
        raise ValidationError("At least one positive tier is required")
    settings.ui.tiers = sorted(set(tiers))

    # Trailing slashes break the f-string URL joins in the clients.
    settings.shoko.url = settings.shoko.url.rstrip("/")
    settings.sonarr.url = settings.sonarr.url.rstrip("/")
