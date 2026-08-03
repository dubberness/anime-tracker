"""Deciding what autobrr gets told to grab."""

from core import autobrr


def entry(anilist_id, title="Show", owned=False, title_alt=""):
    return {
        "anilist_id": anilist_id,
        "title": title,
        "title_alt": title_alt,
        "owned": owned,
        "mal_id": str(anilist_id),
        "anidb_id": "",
    }


def block(entries):
    return {"season": "SUMMER", "year": 2026, "sorts": {"popularity": entries}}


# ==========================
# Ownership recheck
# ==========================

def test_a_tracked_show_shoko_now_has_is_owned():
    row = {"mal_id": "100", "anidb_id": "200"}
    assert autobrr.is_now_owned(row, {"100"}, set()) is True
    assert autobrr.is_now_owned(row, set(), {"200"}) is True


def test_a_tracked_show_shoko_lacks_is_not_owned():
    row = {"mal_id": "100", "anidb_id": "200"}
    assert autobrr.is_now_owned(row, {"999"}, {"888"}) is False


def test_a_row_with_no_ids_is_never_owned():
    """A show too new to be mapped can't be matched, so it stays tracked."""
    assert autobrr.is_now_owned({"mal_id": "", "anidb_id": ""}, {"1"}, {"2"}) is False


# ==========================
# Auto-seeding
# ==========================

def test_auto_seed_takes_the_most_popular_missing_shows():
    candidates = autobrr.auto_seed_candidates(
        block([entry(1), entry(2), entry(3)]), limit=2
    )
    assert [c["anilist_id"] for c in candidates] == [1, 2]


def test_auto_seed_skips_shows_already_in_shoko():
    candidates = autobrr.auto_seed_candidates(
        block([entry(1, owned=True), entry(2), entry(3)]), limit=2
    )
    assert [c["anilist_id"] for c in candidates] == [2, 3]


def test_auto_seed_skips_excluded_shows():
    """An untracked auto-pick must not come straight back on the next run."""
    candidates = autobrr.auto_seed_candidates(
        block([entry(1), entry(2), entry(3)]), excluded_ids={2}, limit=3
    )
    assert [c["anilist_id"] for c in candidates] == [1, 3]


def test_auto_seed_handles_a_missing_season_block():
    assert autobrr.auto_seed_candidates(None) == []
    assert autobrr.auto_seed_candidates({}) == []


def test_auto_seed_with_a_zero_limit_tracks_nothing():
    assert autobrr.auto_seed_candidates(block([entry(1)]), limit=0) == []


# ==========================
# List body
# ==========================

def test_list_has_one_title_per_line():
    text = autobrr.build_list_text([
        {"title": "Show A", "title_alt": ""},
        {"title": "Show B", "title_alt": ""},
    ])
    assert text == "Show A\nShow B"


def test_both_spellings_are_listed_when_they_differ():
    text = autobrr.build_list_text([
        {"title": "Frieren", "title_alt": "Sousou no Frieren"},
    ])
    assert text == "Frieren\nSousou no Frieren"


def test_an_identical_alt_title_is_not_duplicated():
    text = autobrr.build_list_text([{"title": "Bleach", "title_alt": "Bleach"}])
    assert text == "Bleach"


def test_an_empty_list_is_an_empty_body():
    assert autobrr.build_list_text([]) == ""


def test_rows_without_a_title_are_skipped():
    text = autobrr.build_list_text([
        {"title": "", "title_alt": "Orphan"},
        {"title": "Real Show", "title_alt": ""},
    ])
    assert text == "Real Show"
