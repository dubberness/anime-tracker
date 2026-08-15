"""The Kometa Anime-IDs lookup."""

import json

import pytest

import config as config_mod
from clients import mappings as mapping_client

# Shape of the real file: the AniDB ID is the object key, and the value holds
# every other ID. Trimmed to the fields the app reads.
SAMPLE = {
    "1": {"tvdb_id": 72025, "tvdb_season": 1, "mal_id": 290, "anilist_id": 290},
    "2": {"tvdb_id": 70973, "tvdb_season": 1, "mal_id": 300, "anilist_id": 300},
    "3": {"mal_id": 1225},                      # no anilist_id - unusable
    "4": {"tvdb_season": 0, "anilist_id": 400},  # no TVDB ID
}


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "anime_ids.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")

    mapping_settings = config_mod.MappingSettings()
    mapping_settings.path = str(path)
    mapping_settings.auto_download = False
    return mapping_settings


def test_lookup_is_keyed_by_anilist_id(settings):
    lookup = mapping_client.load(settings)
    assert set(lookup) == {290, 300, 400}


def test_anidb_id_comes_from_the_object_key(settings):
    """It is not a field on the value, so it has to be folded in on load."""
    lookup = mapping_client.load(settings)
    assert lookup[290]["anidb_id"] == "1"
    assert lookup[300]["anidb_id"] == "2"


def test_other_ids_survive(settings):
    lookup = mapping_client.load(settings)
    assert lookup[290]["mal_id"] == 290
    assert lookup[290]["tvdb_id"] == 72025
    assert lookup[290]["tvdb_season"] == 1
    assert "tvdb_id" not in lookup[400]


def test_a_file_with_no_anilist_ids_is_rejected(tmp_path, settings):
    path = tmp_path / "anime_ids.json"
    path.write_text(json.dumps({"1": {"mal_id": 5}}), encoding="utf-8")

    with pytest.raises(mapping_client.MappingError):
        mapping_client.load(settings)


def test_a_missing_file_raises(settings):
    settings.path = settings.path + ".nope"
    with pytest.raises(mapping_client.MappingError):
        mapping_client.load(settings)


def write(tmp_path, settings, raw):
    (tmp_path / "anime_ids.json").write_text(json.dumps(raw), encoding="utf-8")
    return mapping_client.load(settings)


def test_two_anidb_rows_naming_one_anilist_id_prefer_the_one_with_a_mal_id(
        tmp_path, settings):
    """Only one row can win the key, and the MAL ID is the other half of the
    ownership rule - a bare last-wins would pick arbitrarily."""
    lookup = write(tmp_path, settings, {
        "10": {"anilist_id": 500},
        "11": {"anilist_id": 500, "mal_id": 900},
    })

    assert lookup[500]["mal_id"] == 900
    assert lookup[500]["anidb_id"] == "11"


def test_the_preference_holds_whichever_order_the_rows_appear_in(
        tmp_path, settings):
    lookup = write(tmp_path, settings, {
        "11": {"anilist_id": 500, "mal_id": 900},
        "10": {"anilist_id": 500},
    })

    assert lookup[500]["mal_id"] == 900
    assert lookup[500]["anidb_id"] == "11"


def test_with_nothing_to_choose_between_them_the_first_row_wins(
        tmp_path, settings):
    """Stable rather than arbitrary, so a mismatch is at least reproducible."""
    lookup = write(tmp_path, settings, {
        "10": {"anilist_id": 500},
        "11": {"anilist_id": 500},
    })

    assert lookup[500]["anidb_id"] == "10"
