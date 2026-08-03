"""Config schema, legacy migration and env overrides."""

import json

import pytest

import config as config_mod

LEGACY = {
    "ShokoURL": "http://192.168.5.145:8111/",
    "APIKey": "shoko-key-123",
    "MappingFile": "/config/anime_ids.json",
    "SonarrURL": "http://192.168.5.145:8989",
    "SonarrAPIKey": "sonarr-key-456",
}


def test_detects_legacy_config():
    assert config_mod.is_legacy(LEGACY)
    assert not config_mod.is_legacy({"version": 2, "shoko": {}})
    assert not config_mod.is_legacy({})


def test_migrates_every_legacy_key():
    settings = config_mod.settings_from_dict(dict(LEGACY))

    assert settings.shoko.url == "http://192.168.5.145:8111/"
    assert settings.shoko.api_key == "shoko-key-123"
    assert settings.sonarr.url == "http://192.168.5.145:8989"
    assert settings.sonarr.api_key == "sonarr-key-456"
    assert settings.sonarr.enabled is True
    assert settings.mappings.path == "/config/anime_ids.json"
    assert settings.version == config_mod.SCHEMA_VERSION


def test_migration_disables_sonarr_when_absent():
    settings = config_mod.settings_from_dict(
        {"ShokoURL": "http://shoko:8111", "APIKey": "k"}
    )
    assert settings.sonarr.enabled is False
    assert settings.sonarr.configured is False


def test_migration_ignores_placeholder_sonarr():
    settings = config_mod.settings_from_dict(dict(
        LEGACY, SonarrURL="http://CHANGE_ME:8989"
    ))
    assert settings.sonarr.enabled is False


def test_load_migrates_and_backs_up_the_original(runtime, clean_env):
    with open(runtime.config_file, "w", encoding="utf-8") as fh:
        json.dump(LEGACY, fh)

    store = config_mod.ConfigStore(runtime)
    settings = store.load()

    assert settings.shoko.api_key == "shoko-key-123"

    # The original is preserved, and the file on disk is now v2.
    with open(runtime.config_file + ".v1.bak", encoding="utf-8") as fh:
        assert json.load(fh) == LEGACY

    with open(runtime.config_file, encoding="utf-8") as fh:
        written = json.load(fh)
    assert written["version"] == config_mod.SCHEMA_VERSION
    assert written["shoko"]["url"] == "http://192.168.5.145:8111/"


def test_missing_config_writes_a_template_and_stays_unconfigured(runtime, clean_env):
    store = config_mod.ConfigStore(runtime)
    settings = store.load()

    assert settings.is_configured is False
    assert store.runtime.config_file
    # A template is written so the file exists to be edited by hand.
    with open(runtime.config_file, encoding="utf-8") as fh:
        assert json.load(fh)["version"] == config_mod.SCHEMA_VERSION


def test_mapping_path_defaults_into_the_config_dir(runtime, clean_env):
    store = config_mod.ConfigStore(runtime)
    settings = store.load()
    assert settings.mappings.path.endswith("anime_ids.json")
    assert runtime.config_dir in settings.mappings.path


def test_env_overrides_win_and_are_reported(runtime, clean_env):
    clean_env.setenv("MIN_POPULARITY", "999")
    clean_env.setenv("CRON_SCHEDULE", "*/15 * * * *")

    store = config_mod.ConfigStore(runtime)
    settings = store.load()

    assert settings.anilist.min_popularity == 999
    assert settings.schedule.cron == "*/15 * * * *"
    assert "anilist.min_popularity" in store.env_locked
    assert "schedule.cron" in store.env_locked


def test_env_locked_fields_cannot_be_overwritten_by_the_ui(runtime, clean_env):
    clean_env.setenv("MIN_POPULARITY", "999")

    store = config_mod.ConfigStore(runtime)
    store.load()
    store.update({"anilist": {"min_popularity": 10}})

    assert store.settings.anilist.min_popularity == 999


def test_update_keeps_the_existing_secret_when_masked(runtime, clean_env):
    store = config_mod.ConfigStore(runtime)
    store.load()
    store.update({"shoko": {"url": "http://shoko:8111", "api_key": "real-key"}})

    masked = store.settings.redacted()["shoko"]["api_key"]
    assert masked.startswith("****")

    # Submitting the masked value back must not overwrite the real one.
    store.update({"shoko": {"url": "http://shoko:8111", "api_key": masked}})
    assert store.settings.shoko.api_key == "real-key"

    # Nor should an empty string.
    store.update({"shoko": {"url": "http://shoko:8111", "api_key": ""}})
    assert store.settings.shoko.api_key == "real-key"


def test_update_persists_to_disk(runtime, clean_env):
    store = config_mod.ConfigStore(runtime)
    store.load()
    store.update({"shoko": {"url": "http://shoko:8111"}})

    reloaded = config_mod.ConfigStore(runtime)
    assert reloaded.load().shoko.url == "http://shoko:8111"


def test_validation_rejects_bad_values():
    settings = config_mod.Settings()

    settings.schedule.cron = "not a cron"
    with pytest.raises(config_mod.ValidationError):
        config_mod.validate(settings)

    settings = config_mod.Settings()
    settings.shoko.url = "192.168.1.1:8111"
    with pytest.raises(config_mod.ValidationError):
        config_mod.validate(settings)

    settings = config_mod.Settings()
    settings.anilist.formats = ["NOPE"]
    with pytest.raises(config_mod.ValidationError):
        config_mod.validate(settings)

    settings = config_mod.Settings()
    settings.anilist.max_results = 0
    with pytest.raises(config_mod.ValidationError):
        config_mod.validate(settings)


def test_validation_normalises_urls_and_tiers():
    settings = config_mod.Settings()
    settings.shoko.url = "http://shoko:8111/"
    settings.ui.tiers = [500, 100, 100, 250]

    config_mod.validate(settings)

    assert settings.shoko.url == "http://shoko:8111"
    assert settings.ui.tiers == [100, 250, 500]


def test_corrupt_config_falls_back_to_defaults(runtime, clean_env):
    with open(runtime.config_file, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")

    store = config_mod.ConfigStore(runtime)
    settings = store.load()
    assert settings.is_configured is False
