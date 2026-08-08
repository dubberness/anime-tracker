"""Deciding what autobrr gets told to grab."""

from datetime import datetime, timedelta

from core import autobrr

NOW = datetime(2026, 8, 8, 4, 0, 0)


def entry(anilist_id, title="Show", owned=False, title_alt="", sequel=False,
          status="RELEASING", episodes_aired=None, episodes_local=0,
          long_runner=False):
    return {
        "anilist_id": anilist_id,
        "title": title,
        "title_alt": title_alt,
        "owned": owned,
        "sequel_of_owned": sequel,
        "mal_id": str(anilist_id),
        "anidb_id": "",
        "status": status,
        "episodes_aired": episodes_aired,
        "episodes_local": episodes_local,
        "is_long_runner": long_runner,
    }


def block(entries, current=False, upcoming=True):
    return {
        "season": "FALL",
        "year": 2026,
        "is_current": current,
        "is_upcoming": upcoming,
        "sorts": {"popularity": entries},
    }


def upcoming(entries):
    """A seasons list carrying just the upcoming block."""
    return [block(entries)]


def row(anilist_id=1, title="Show", mal_id="100", anidb_id="",
        status="", status_at=None, added_days_ago=1):
    return {
        "anilist_id": anilist_id,
        "title": title,
        "title_alt": "",
        "mal_id": mal_id,
        "anidb_id": anidb_id,
        "source": "auto",
        "added_at": (NOW - timedelta(days=added_days_ago)).isoformat(),
        "status": status,
        "status_at": status_at or "",
    }


def finished_at(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def ids(candidates):
    return [c["anilist_id"] for c in candidates]


# ==========================
# Ownership recheck
# ==========================

def test_a_tracked_show_shoko_now_has_is_owned():
    tracked = {"mal_id": "100", "anidb_id": "200"}
    assert autobrr.is_now_owned(tracked, {"100"}, set()) is True
    assert autobrr.is_now_owned(tracked, set(), {"200"}) is True


def test_a_tracked_show_shoko_lacks_is_not_owned():
    tracked = {"mal_id": "100", "anidb_id": "200"}
    assert autobrr.is_now_owned(tracked, {"999"}, {"888"}) is False


def test_a_row_with_no_ids_is_never_owned():
    """A show too new to be mapped can't be matched, so it stays tracked."""
    assert autobrr.is_now_owned({"mal_id": "", "anidb_id": ""}, {"1"}, {"2"}) is False


# ==========================
# Auto-seeding
# ==========================

def test_auto_seed_takes_the_most_popular_missing_shows():
    assert ids(autobrr.auto_seed_candidates(
        [entry(1), entry(2), entry(3)], limit=2
    )) == [1, 2]


def test_auto_seed_keeps_a_show_shoko_only_partly_has():
    """The split-cour case.

    Part two is a separate AniList entry that the mapping file points at part
    one's MAL ID, so Shoko reports it owned the moment part one imported.
    Skipping on ownership made exactly these impossible to track.
    """
    assert ids(autobrr.auto_seed_candidates(
        [entry(1, owned=True, episodes_aired=6, episodes_local=2)], limit=2
    )) == [1]


def test_auto_seed_skips_a_show_shoko_has_complete():
    assert ids(autobrr.auto_seed_candidates(
        [entry(1, owned=True, episodes_aired=6, episodes_local=6),
         entry(2), entry(3)],
        limit=2,
    )) == [2, 3]


def test_auto_seed_skips_shows_that_are_not_airing():
    """Otherwise an aged-out show would be re-added the very next run."""
    assert ids(autobrr.auto_seed_candidates(
        [entry(1, status="FINISHED"), entry(2, status="CANCELLED"), entry(3)],
        limit=5,
    )) == [3]


def test_auto_seed_skips_long_runners():
    """Auto-tracking One Piece means autobrr chasing eleven hundred episodes."""
    assert ids(autobrr.auto_seed_candidates(
        [entry(1, long_runner=True), entry(2)], limit=5
    )) == [2]


def test_auto_seed_skips_excluded_shows():
    """An untracked auto-pick must not come straight back on the next run."""
    assert ids(autobrr.auto_seed_candidates(
        [entry(1), entry(2), entry(3)], excluded_ids={2}, limit=3
    )) == [1, 3]


def test_auto_seed_handles_missing_data():
    assert autobrr.auto_seed_candidates(None) == []
    assert autobrr.auto_seed_candidates([]) == []
    assert autobrr.auto_seed_candidates([], None) == []
    assert autobrr.auto_seed_candidates([], [{"season": "FALL"}]) == []


def test_auto_seed_with_a_zero_limit_tracks_nothing():
    assert autobrr.auto_seed_candidates([entry(1)], upcoming([entry(8)]),
                                        limit=0) == []


# -- sequel rescue --

def test_a_sequel_inside_the_top_n_uses_a_slot_like_anything_else():
    """No reordering - within the cutoff it's just one of the popular ones."""
    assert ids(autobrr.auto_seed_candidates(
        [entry(1), entry(2, sequel=True), entry(3)], limit=2
    )) == [1, 2]


def test_a_sequel_below_the_cutoff_is_tracked_anyway():
    """The case popularity alone always misses: the next season of a show with
    a small audience, ranked too low to be picked up."""
    assert ids(autobrr.auto_seed_candidates(
        [entry(1), entry(2), entry(3, sequel=True), entry(4)], limit=2
    )) == [1, 2, 3]


def test_every_sequel_below_the_cutoff_is_rescued_not_just_the_first():
    assert ids(autobrr.auto_seed_candidates(
        [entry(1), entry(2), entry(3, sequel=True), entry(4),
         entry(5, sequel=True)],
        limit=2,
    )) == [1, 2, 3, 5]


def test_an_upcoming_sequel_below_the_cutoff_is_rescued_too():
    assert ids(autobrr.auto_seed_candidates(
        [entry(1), entry(2)],
        upcoming([entry(8), entry(9), entry(10, sequel=True)]),
        limit=2,
    )) == [1, 2, 8, 9, 10]


# -- the two sources --

def test_the_limit_is_per_source_not_shared():
    """Each source gets its own allowance, so what's airing can't eat the
    upcoming season's slots by simply having more popular shows in it."""
    assert ids(autobrr.auto_seed_candidates(
        [entry(1), entry(2), entry(3)],
        upcoming([entry(8), entry(9), entry(10)]),
        limit=2,
    )) == [1, 2, 8, 9]


def test_a_show_in_both_sources_is_only_seeded_once():
    assert ids(autobrr.auto_seed_candidates(
        [entry(1), entry(2)], upcoming([entry(1), entry(9)]), limit=10
    )) == [1, 2, 9]


def test_owned_and_excluded_are_honoured_in_the_upcoming_block_too():
    assert ids(autobrr.auto_seed_candidates(
        [entry(1)],
        upcoming([entry(8, owned=True), entry(9), entry(10)]),
        excluded_ids={9},
        limit=10,
    )) == [1, 10]


def test_the_current_season_block_is_not_a_seed_source():
    """Superseded by the airing list, which sees carryovers the chart can't.

    AniList tags media with its *start* season, so a two-cour show drops off
    the current chart halfway through its run - seeding from that chart is
    what let an airing show go untracked in the first place.
    """
    seasons = [block([entry(50), entry(51)], current=True, upcoming=False)]
    assert ids(autobrr.auto_seed_candidates([entry(1)], seasons, limit=10)) == [1]


def test_an_upcoming_show_seeds_even_when_the_airing_fetch_failed():
    """The upcoming block is independent data - losing one shouldn't lose both."""
    assert ids(autobrr.auto_seed_candidates(
        None, upcoming([entry(8), entry(9)]), limit=10
    )) == [8, 9]


def test_upcoming_shows_are_not_filtered_on_airing_status():
    """Everything in the upcoming block is NOT_YET_RELEASED by definition."""
    assert ids(autobrr.auto_seed_candidates(
        [], upcoming([entry(8, status="NOT_YET_RELEASED")]), limit=10
    )) == [8]


def test_results_from_before_the_upcoming_flag_seed_nothing_extra():
    """Old persisted runs have no is_upcoming key - that must degrade, not fail."""
    stale = [{"season": "SUMMER", "year": 2026, "is_current": True,
              "sorts": {"popularity": [entry(50)]}}]
    assert ids(autobrr.auto_seed_candidates([entry(1)], stale, limit=10)) == [1]


# ==========================
# Staying tracked
# ==========================

def test_an_airing_show_stays_tracked_even_when_shoko_has_it():
    """The defect that lost the Slime episode.

    Shoko registers a series on its first episode, so the old rule untracked a
    show one week into a twelve week run.
    """
    assert autobrr.should_stay_tracked(
        row(), "RELEASING", owned=True, complete=False, now=NOW
    ) == (True, "airing")


def test_an_airing_show_stays_tracked_when_shoko_lacks_it():
    keep, _ = autobrr.should_stay_tracked(row(), "RELEASING", owned=False, now=NOW)
    assert keep is True


def test_an_airing_show_stays_tracked_even_when_complete():
    """More is still coming, so there is no such thing as done yet."""
    keep, _ = autobrr.should_stay_tracked(
        row(), "RELEASING", owned=True, complete=True, now=NOW
    )
    assert keep is True


def test_an_unaired_show_stays_tracked():
    assert autobrr.should_stay_tracked(
        row(), "NOT_YET_RELEASED", owned=False, now=NOW
    ) == (True, "not aired yet")


def test_a_show_on_hiatus_stays_tracked():
    assert autobrr.should_stay_tracked(row(), "HIATUS", owned=False, now=NOW) \
        == (True, "on hiatus")


def test_a_finished_and_complete_show_is_dropped():
    assert autobrr.should_stay_tracked(
        row(status="FINISHED", status_at=finished_at(1)),
        "FINISHED", owned=True, complete=True, now=NOW,
    ) == (False, "complete")


def test_a_finished_but_incomplete_show_stays_through_the_grace_window():
    """AniList marks FINISHED when the finale airs, not when it is grabbable."""
    assert autobrr.should_stay_tracked(
        row(status="FINISHED", status_at=finished_at(13)),
        "FINISHED", owned=True, complete=False, now=NOW, grace_days=14,
    ) == (True, "finished, in grace")


def test_a_finished_show_is_dropped_once_the_grace_window_passes():
    assert autobrr.should_stay_tracked(
        row(status="FINISHED", status_at=finished_at(15)),
        "FINISHED", owned=False, complete=False, now=NOW, grace_days=14,
    ) == (False, "finished, not coming")


def test_a_zero_grace_window_drops_a_finished_show_immediately():
    keep, _ = autobrr.should_stay_tracked(
        row(status="FINISHED", status_at=finished_at(0)),
        "FINISHED", owned=False, complete=False, now=NOW, grace_days=0,
    )
    assert keep is False


def test_a_cancelled_show_is_dropped():
    assert autobrr.should_stay_tracked(row(), "CANCELLED", owned=False, now=NOW) \
        == (False, "cancelled")


def test_an_unrecognised_status_fails_open():
    keep, _ = autobrr.should_stay_tracked(row(), "SOMETHING_NEW", owned=True, now=NOW)
    assert keep is True


def test_an_unknown_status_keeps_the_show():
    assert autobrr.should_stay_tracked(row(), None, owned=True, now=NOW) \
        == (True, "status unknown")


def test_a_row_never_identified_is_dropped_eventually():
    assert autobrr.should_stay_tracked(
        row(added_days_ago=200), None, owned=False, now=NOW, anilist_answered=True
    ) == (False, "never identified")


def test_a_recently_added_unidentified_row_is_kept():
    """New shows routinely take a run or two to appear in the mapping file."""
    keep, _ = autobrr.should_stay_tracked(
        row(added_days_ago=10), None, owned=False, now=NOW, anilist_answered=True
    )
    assert keep is True


def test_completeness_falls_back_to_ownership_when_not_given():
    assert autobrr.should_stay_tracked(
        row(status="FINISHED", status_at=finished_at(30)),
        "FINISHED", owned=True, now=NOW,
    ) == (False, "complete")


# ==========================
# Prune plan
# ==========================

def test_a_failed_anilist_fetch_never_untracks_anything():
    """The highest-consequence path in the whole feature.

    An empty list hands autobrr nothing to match and silently stops every
    grab, so an outage has to be a no-op rather than a mass untrack.
    """
    rows = [
        row(1, mal_id="100", status="FINISHED", status_at=finished_at(90)),
        row(2, mal_id="200", status="FINISHED", status_at=finished_at(90)),
    ]
    assert autobrr.prune_plan(rows, None, {"100", "200"}, set(), now=NOW) == []


def test_prune_plan_drops_a_finished_complete_show_with_a_reason():
    rows = [row(1, mal_id="100", status="FINISHED", status_at=finished_at(1))]
    statuses = {1: {"status": "FINISHED", "episodes_aired": 12}}

    drops = autobrr.prune_plan(
        rows, statuses, {"100"}, set(), local_by_mal={"100": 12}, now=NOW
    )

    assert [(r["anilist_id"], reason) for r, reason in drops] == [(1, "complete")]


def test_prune_plan_keeps_an_airing_show_shoko_has_started():
    rows = [row(1, mal_id="100", status="RELEASING")]
    statuses = {1: {"status": "RELEASING", "episodes_aired": 6}}

    assert autobrr.prune_plan(
        rows, statuses, {"100"}, set(), local_by_mal={"100": 2}, now=NOW
    ) == []


def test_prune_plan_keeps_a_finished_show_still_missing_episodes():
    rows = [row(1, mal_id="100", status="FINISHED", status_at=finished_at(2))]
    statuses = {1: {"status": "FINISHED", "episodes_aired": 12}}

    assert autobrr.prune_plan(
        rows, statuses, {"100"}, set(), local_by_mal={"100": 11}, now=NOW
    ) == []


def test_prune_plan_leaves_rows_anilist_did_not_return():
    """Answering is not the same as knowing about every row."""
    rows = [row(1, mal_id="100", added_days_ago=5)]
    assert autobrr.prune_plan(rows, {}, {"100"}, set(), now=NOW) == []


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
