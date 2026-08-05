"""Deciding what autobrr gets told to grab."""

from core import autobrr


def entry(anilist_id, title="Show", owned=False, title_alt="", sequel=False):
    return {
        "anilist_id": anilist_id,
        "title": title,
        "title_alt": title_alt,
        "owned": owned,
        "sequel_of_owned": sequel,
        "mal_id": str(anilist_id),
        "anidb_id": "",
    }


def block(entries, current=True, upcoming=False):
    return {
        "season": "SUMMER",
        "year": 2026,
        "is_current": current,
        "is_upcoming": upcoming,
        "sorts": {"popularity": entries},
    }


def seasons(entries, upcoming_entries=None):
    """A seasons list as the runner builds it: current block, then the next."""
    blocks = [block(entries)]
    if upcoming_entries is not None:
        blocks.append(block(upcoming_entries, current=False, upcoming=True))
    return blocks


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
        seasons([entry(1), entry(2), entry(3)]), limit=2
    )
    assert [c["anilist_id"] for c in candidates] == [1, 2]


def test_auto_seed_skips_shows_already_in_shoko():
    candidates = autobrr.auto_seed_candidates(
        seasons([entry(1, owned=True), entry(2), entry(3)]), limit=2
    )
    assert [c["anilist_id"] for c in candidates] == [2, 3]


def test_auto_seed_skips_excluded_shows():
    """An untracked auto-pick must not come straight back on the next run."""
    candidates = autobrr.auto_seed_candidates(
        seasons([entry(1), entry(2), entry(3)]), excluded_ids={2}, limit=3
    )
    assert [c["anilist_id"] for c in candidates] == [1, 3]


def test_auto_seed_handles_a_missing_season_block():
    assert autobrr.auto_seed_candidates(None) == []
    assert autobrr.auto_seed_candidates([]) == []
    assert autobrr.auto_seed_candidates([{"season": "SUMMER"}]) == []


def test_auto_seed_with_a_zero_limit_tracks_nothing():
    assert autobrr.auto_seed_candidates(seasons([entry(1)]), limit=0) == []


def test_a_sequel_inside_the_top_n_uses_a_slot_like_anything_else():
    """No reordering - within the cutoff it's just one of the popular ones."""
    candidates = autobrr.auto_seed_candidates(
        seasons([entry(1), entry(2, sequel=True), entry(3)]), limit=2
    )
    assert [c["anilist_id"] for c in candidates] == [1, 2]


def test_a_sequel_below_the_cutoff_is_tracked_anyway():
    """The case popularity alone always misses: the next season of a show with
    a small audience, ranked too low to be picked up."""
    candidates = autobrr.auto_seed_candidates(
        seasons([entry(1), entry(2), entry(3, sequel=True), entry(4)]), limit=2
    )
    assert [c["anilist_id"] for c in candidates] == [1, 2, 3]


def test_every_sequel_below_the_cutoff_is_rescued_not_just_the_first():
    candidates = autobrr.auto_seed_candidates(
        seasons([entry(1), entry(2), entry(3, sequel=True), entry(4),
                 entry(5, sequel=True)]),
        limit=2,
    )
    assert [c["anilist_id"] for c in candidates] == [1, 2, 3, 5]


def test_an_upcoming_sequel_below_the_cutoff_is_rescued_too():
    candidates = autobrr.auto_seed_candidates(
        seasons([entry(1), entry(2)],
                upcoming_entries=[entry(8), entry(9), entry(10, sequel=True)]),
        limit=2,
    )
    assert [c["anilist_id"] for c in candidates] == [1, 2, 8, 9, 10]


def test_the_limit_is_per_season_not_shared():
    """Each season gets its own allowance, so the current one can't eat the
    upcoming one's slots by simply having more popular shows in it."""
    candidates = autobrr.auto_seed_candidates(
        seasons([entry(1), entry(2), entry(3)],
                upcoming_entries=[entry(8), entry(9), entry(10)]),
        limit=2,
    )
    assert [c["anilist_id"] for c in candidates] == [1, 2, 8, 9]


def test_a_show_in_both_blocks_is_only_seeded_once():
    candidates = autobrr.auto_seed_candidates(
        seasons([entry(1), entry(2)], upcoming_entries=[entry(1), entry(9)]),
        limit=10,
    )
    assert [c["anilist_id"] for c in candidates] == [1, 2, 9]


def test_owned_and_excluded_are_honoured_in_the_upcoming_block_too():
    candidates = autobrr.auto_seed_candidates(
        seasons(
            [entry(1)],
            upcoming_entries=[entry(8, owned=True), entry(9), entry(10)],
        ),
        excluded_ids={9},
        limit=10,
    )
    assert [c["anilist_id"] for c in candidates] == [1, 10]


def test_results_from_before_the_upcoming_flag_seed_the_current_season_only():
    """Old persisted runs have no is_upcoming key - that must degrade, not fail."""
    stale = [{"season": "SUMMER", "year": 2026, "is_current": True,
              "sorts": {"popularity": [entry(1), entry(2)]}}]
    candidates = autobrr.auto_seed_candidates(stale, limit=10)
    assert [c["anilist_id"] for c in candidates] == [1, 2]


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
