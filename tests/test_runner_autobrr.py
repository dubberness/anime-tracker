"""The runner's autobrr steps, against real storage.

core/autobrr.py holds the decisions and is tested directly; this covers the
wiring around them - that a failed AniList fetch really does reach prune_plan
as None, and that an airing show Shoko has started really does get tracked.
"""

import config as config_mod
from runner import Runner
from state import RunState
from storage import Storage


def make_runner(runtime):
    store = config_mod.ConfigStore(runtime)
    store.load()
    storage = Storage(runtime.database_file, runtime.results_file)
    return Runner(store, storage, RunState()), storage


def airing_entry(anilist_id, title, mal_id, owned=False, aired=6, local=0,
                 status="RELEASING", long_runner=False):
    return {
        "anilist_id": anilist_id, "title": title, "title_alt": "",
        "mal_id": mal_id, "anidb_id": "", "owned": owned,
        "episodes_aired": aired, "episodes_local": local,
        "status": status, "is_long_runner": long_runner,
    }


# ==========================
# Pruning
# ==========================

def test_a_failed_airing_fetch_untracks_nothing(runtime):
    """The outage path.

    An empty list stops autobrr matching anything at all, so a run that could
    not establish status has to leave the list exactly as it found it.
    """
    runner, storage = make_runner(runtime)
    storage.track_autobrr(1, "Finished Show", mal_id="100")

    removed = runner._prune_tracked(None, {"100"}, set(), {"100": 12}, {})

    assert removed == 0
    assert storage.autobrr_tracked_ids() == {1}


def test_an_airing_show_survives_shoko_picking_it_up(runtime):
    """The defect that lost the Slime episode, end to end."""
    runner, storage = make_runner(runtime)
    storage.track_autobrr(1, "Slime", mal_id="100")

    statuses = {1: {"status": "RELEASING", "episodes_aired": 17}}
    removed = runner._prune_tracked(statuses, {"100"}, set(), {"100": 14}, {})

    assert removed == 0
    assert storage.autobrr_tracked_ids() == {1}


def test_a_finished_and_complete_show_is_untracked(runtime):
    runner, storage = make_runner(runtime)
    storage.track_autobrr(1, "Done", mal_id="100")

    statuses = {1: {"status": "FINISHED", "episodes_aired": 12}}
    removed = runner._prune_tracked(statuses, {"100"}, set(), {"100": 12}, {})

    assert removed == 1
    assert storage.autobrr_tracked_ids() == set()


def test_an_untracked_show_is_not_excluded(runtime):
    """Aged-out shows have to stay re-trackable."""
    runner, storage = make_runner(runtime)
    storage.track_autobrr(1, "Done", mal_id="100")

    statuses = {1: {"status": "FINISHED", "episodes_aired": 12}}
    runner._prune_tracked(statuses, {"100"}, set(), {"100": 12}, {})

    assert storage.autobrr_excluded_ids() == set()


def test_recording_status_stamps_tracked_rows(runtime):
    runner, storage = make_runner(runtime)
    storage.track_autobrr(1, "Show", mal_id="100")

    runner._record_statuses({1: {"status": "FINISHED", "episodes_aired": 12}})

    assert storage.list_autobrr_tracked()[0]["status"] == "FINISHED"


# ==========================
# Auto-seeding
# ==========================

def test_auto_seed_tracks_an_airing_show_shoko_only_partly_has(runtime):
    """Owning part one used to make a split-cour sequel untrackable."""
    runner, storage = make_runner(runtime)

    added = runner._auto_seed_tracked(
        [airing_entry(1, "Slime", "100", owned=True, aired=17, local=14)],
        [], limit=10,
    )

    assert added == 1
    assert storage.autobrr_tracked_ids() == {1}


def test_auto_seed_skips_a_complete_show(runtime):
    runner, storage = make_runner(runtime)

    added = runner._auto_seed_tracked(
        [airing_entry(1, "Done", "100", owned=True, aired=12, local=12)],
        [], limit=10,
    )

    assert added == 0
    assert storage.autobrr_tracked_ids() == set()


def test_auto_seed_falls_back_to_the_upcoming_season_alone(runtime):
    """A failed airing fetch loses that source but not the upcoming block, and
    never falls back to the current season's chart - doing that would silently
    reintroduce the carryover blind spot."""
    runner, storage = make_runner(runtime)
    seasons = [
        {"is_current": True, "is_upcoming": False,
         "sorts": {"popularity": [airing_entry(50, "Current", "500")]}},
        {"is_current": False, "is_upcoming": True,
         "sorts": {"popularity": [
             airing_entry(8, "Next", "800", status="NOT_YET_RELEASED")]}},
    ]

    added = runner._auto_seed_tracked(None, seasons, limit=10)

    assert added == 1
    assert storage.autobrr_tracked_ids() == {8}


def test_auto_seed_does_nothing_with_no_data_at_all(runtime):
    runner, storage = make_runner(runtime)

    assert runner._auto_seed_tracked(None, [], limit=10) == 0
    assert storage.autobrr_tracked_ids() == set()
