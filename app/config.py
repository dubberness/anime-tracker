"""Configuration loading, from JSON file with environment overrides."""

import json
import os
import sys
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Config:
    shoko_url: str = ""
    shoko_api_key: str = ""
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    mapping_file: str = "/config/anime_ids.json"

    config_dir: str = "/config"
    output_dir: str = "/output"
    cache_file: str = ""
    snapshot_file: str = ""

    anilist_url: str = "https://graphql.anilist.co"
    max_results: int = 1000
    min_popularity: int = 50000
    cache_max_age_hours: int = 24
    page_size: int = 50
    request_delay_ms: int = 500

    max_retries: int = 4
    initial_backoff_seconds: int = 2

    cron_schedule: str = "0 4 * * *"
    run_on_start: bool = True
    serve_web: bool = True
    web_port: int = 8080

    warnings: list = field(default_factory=list)

    @property
    def sonarr_enabled(self) -> bool:
        return bool(self.sonarr_url and self.sonarr_api_key
                    and "CHANGE_ME" not in self.sonarr_url)


CONFIG_TEMPLATE = {
    "ShokoURL": "http://CHANGE_ME:8111",
    "APIKey": "YOUR_SHOKO_API_KEY",
    "MappingFile": "/config/anime_ids.json",
    "SonarrURL": "http://CHANGE_ME:8989",
    "SonarrAPIKey": "YOUR_SONARR_API_KEY",
}


def load_config() -> Config:
    cfg = Config()

    cfg.config_dir = os.environ.get("CONFIG_DIR", "/config")
    cfg.output_dir = os.environ.get("OUTPUT_DIR", "/output")

    config_path = os.environ.get(
        "CONFIG_FILE", os.path.join(cfg.config_dir, "config.json")
    )

    if not os.path.exists(config_path):
        os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(CONFIG_TEMPLATE, fh, indent=2)
        print(f"No config found - wrote a template to {config_path}")
        print("Edit it with your real URLs and API keys, then restart.")
        sys.exit(1)

    with open(config_path, encoding="utf-8") as fh:
        raw = json.load(fh)

    cfg.shoko_url = str(raw.get("ShokoURL", "")).rstrip("/")
    cfg.shoko_api_key = str(raw.get("APIKey", ""))
    cfg.sonarr_url = str(raw.get("SonarrURL", "")).rstrip("/")
    cfg.sonarr_api_key = str(raw.get("SonarrAPIKey", ""))
    cfg.mapping_file = str(
        raw.get("MappingFile", os.path.join(cfg.config_dir, "anime_ids.json"))
    )

    # Optional tuning knobs, config file first then env override.
    cfg.max_results = int(raw.get("MaxResults", cfg.max_results))
    cfg.min_popularity = int(raw.get("MinPopularity", cfg.min_popularity))
    cfg.cache_max_age_hours = int(
        raw.get("CacheMaxAgeHours", cfg.cache_max_age_hours)
    )

    cfg.max_results = _env_int("MAX_RESULTS", cfg.max_results)
    cfg.min_popularity = _env_int("MIN_POPULARITY", cfg.min_popularity)
    cfg.cache_max_age_hours = _env_int(
        "CACHE_MAX_AGE_HOURS", cfg.cache_max_age_hours
    )

    cfg.anilist_url = os.environ.get("ANILIST_URL", cfg.anilist_url)

    cfg.cache_file = os.environ.get(
        "CACHE_FILE", os.path.join(cfg.config_dir, "anilist_top_tv_cache.json")
    )
    cfg.snapshot_file = os.environ.get(
        "SNAPSHOT_FILE", os.path.join(cfg.config_dir, "previous_results.json")
    )

    cfg.cron_schedule = os.environ.get("CRON_SCHEDULE", cfg.cron_schedule)
    cfg.run_on_start = _env_bool("RUN_ON_START", cfg.run_on_start)
    cfg.serve_web = _env_bool("SERVE_WEB", cfg.serve_web)
    cfg.web_port = _env_int("WEB_PORT", cfg.web_port)

    if "CHANGE_ME" in cfg.shoko_url or not cfg.shoko_url:
        print(f"Shoko URL is not configured yet - edit {config_path}")
        sys.exit(1)

    if not os.path.exists(cfg.mapping_file):
        print(f"Mapping file not found: {cfg.mapping_file}")
        print("Download the Kometa-Team Anime-IDs mapping and place it there:")
        print("  https://github.com/Kometa-Team/Anime-IDs")
        sys.exit(1)

    os.makedirs(cfg.output_dir, exist_ok=True)

    return cfg
