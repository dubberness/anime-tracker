"""Run history and results persistence."""

from storage import Storage


def make_storage(tmp_path):
    return Storage(str(tmp_path / "history.db"), str(tmp_path / "results.json"))


def test_run_lifecycle_is_recorded(tmp_path):
    store = make_storage(tmp_path)

    run_id = store.start_run()
    store.finish_run(
        run_id, "success",
        stats={"total": 100, "owned": 40, "missing": 60, "completion": 40.0},
        totals={"shoko_shows": 12, "shoko_episodes": 300, "sonarr_shows": 5},
        migration={"migrated": 3, "remaining_size_gb": 12.5},
        duration=4.2,
    )

    runs = store.recent_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["completion"] == 40.0
    assert runs[0]["shoko_episodes"] == 300
    assert runs[0]["duration_seconds"] == 4.2


def test_history_only_includes_successful_runs(tmp_path):
    store = make_storage(tmp_path)

    ok = store.start_run()
    store.finish_run(ok, "success", stats={"completion": 50.0})

    bad = store.start_run()
    store.finish_run(bad, "failed", error="boom")

    history = store.history()
    assert len(history) == 1
    assert history[0]["completion"] == 50.0

    # ...but recent_runs shows both, so failures stay visible.
    assert len(store.recent_runs()) == 2


def test_history_is_oldest_first_for_plotting(tmp_path):
    store = make_storage(tmp_path)

    for completion in (10.0, 20.0, 30.0):
        run_id = store.start_run()
        store.finish_run(run_id, "success", stats={"completion": completion})

    assert [row["completion"] for row in store.history()] == [10.0, 20.0, 30.0]


def test_last_successful_run(tmp_path):
    store = make_storage(tmp_path)
    assert store.last_successful_run() is None

    run_id = store.start_run()
    store.finish_run(run_id, "success", stats={"completion": 12.0})
    assert store.last_successful_run()["completion"] == 12.0


def test_prune_keeps_only_the_most_recent(tmp_path):
    store = make_storage(tmp_path)
    for _ in range(6):
        run_id = store.start_run()
        store.finish_run(run_id, "success", stats={"completion": 1.0})

    store.prune(keep=3)
    assert len(store.recent_runs(limit=50)) == 3


def test_results_round_trip(tmp_path):
    store = make_storage(tmp_path)
    assert store.load_results() is None

    payload = {"entries": [{"mal_id": "1", "owned": True}], "stats": {"total": 1}}
    store.save_results(payload)

    assert store.load_results() == payload


def test_corrupt_results_file_returns_none(tmp_path):
    store = make_storage(tmp_path)
    with open(store.results_file, "w", encoding="utf-8") as fh:
        fh.write("not json")

    assert store.load_results() is None


# ==========================
# Autobrr tracking
# ==========================

def test_tracking_round_trip(tmp_path):
    store = make_storage(tmp_path)
    assert store.list_autobrr_tracked() == []

    store.track_autobrr(1, "Frieren", "Sousou no Frieren", "52991", "17617")
    rows = store.list_autobrr_tracked()

    assert len(rows) == 1
    assert rows[0]["title"] == "Frieren"
    assert rows[0]["title_alt"] == "Sousou no Frieren"
    assert rows[0]["mal_id"] == "52991"
    assert rows[0]["anidb_id"] == "17617"
    assert store.autobrr_tracked_ids() == {1}


def test_untracking_removes_the_row(tmp_path):
    store = make_storage(tmp_path)
    store.track_autobrr(1, "Frieren")
    store.untrack_autobrr(1)

    assert store.list_autobrr_tracked() == []
    assert store.autobrr_excluded_ids() == set()


def test_retracking_updates_details_but_keeps_added_at(tmp_path):
    store = make_storage(tmp_path)
    store.track_autobrr(1, "Placeholder Title", mal_id="", source="auto")
    original = store.list_autobrr_tracked()[0]["added_at"]

    store.track_autobrr(1, "Real Title", "Romaji Title", "500", "600")
    row = store.list_autobrr_tracked()[0]

    assert row["title"] == "Real Title"
    assert row["title_alt"] == "Romaji Title"
    assert row["mal_id"] == "500"
    assert row["added_at"] == original


def test_untracking_with_exclude_records_the_exclusion(tmp_path):
    store = make_storage(tmp_path)
    store.track_autobrr(1, "Frieren", source="auto")
    store.untrack_autobrr(1, exclude=True)

    assert store.list_autobrr_tracked() == []
    assert store.autobrr_excluded_ids() == {1}


def test_tracking_again_clears_an_exclusion(tmp_path):
    """Opting back in has to undo an earlier opt-out completely."""
    store = make_storage(tmp_path)
    store.untrack_autobrr(1, exclude=True)
    assert store.autobrr_excluded_ids() == {1}

    store.track_autobrr(1, "Frieren")

    assert store.autobrr_excluded_ids() == set()
    assert store.autobrr_tracked_ids() == {1}


def test_excluding_twice_does_not_error(tmp_path):
    store = make_storage(tmp_path)
    store.untrack_autobrr(1, exclude=True)
    store.untrack_autobrr(1, exclude=True)

    assert store.autobrr_excluded_ids() == {1}


# ==========================
# Observed AniList status
# ==========================

def test_recording_a_status_stamps_when_it_was_seen(tmp_path):
    store = make_storage(tmp_path)
    store.track_autobrr(1, "Frieren")

    store.record_autobrr_status(1, "RELEASING")

    row = store.list_autobrr_tracked()[0]
    assert row["status"] == "RELEASING"
    assert row["status_at"] != ""


def test_reobserving_the_same_status_does_not_restamp(tmp_path):
    """The grace clock runs from the transition, not from the last sighting."""
    store = make_storage(tmp_path)
    store.track_autobrr(1, "Frieren")
    store.record_autobrr_status(1, "FINISHED")
    first = store.list_autobrr_tracked()[0]["status_at"]

    store.record_autobrr_status(1, "FINISHED")

    assert store.list_autobrr_tracked()[0]["status_at"] == first


def test_changing_status_restamps(tmp_path):
    store = make_storage(tmp_path)
    store.track_autobrr(1, "Frieren")
    store.record_autobrr_status(1, "RELEASING")
    store.record_autobrr_status(1, "FINISHED")

    row = store.list_autobrr_tracked()[0]
    assert row["status"] == "FINISHED"
    assert row["status_at"] != ""


def test_status_columns_are_added_to_a_database_that_predates_them(tmp_path):
    """CREATE TABLE IF NOT EXISTS is a no-op, so upgrades need the ALTER."""
    store = make_storage(tmp_path)

    with store._connect() as conn:
        conn.execute("DROP TABLE autobrr_tracked")
        conn.execute(
            """
            CREATE TABLE autobrr_tracked (
                anilist_id  INTEGER PRIMARY KEY,
                title       TEXT    NOT NULL,
                title_alt   TEXT    NOT NULL DEFAULT '',
                mal_id      TEXT    NOT NULL DEFAULT '',
                anidb_id    TEXT    NOT NULL DEFAULT '',
                source      TEXT    NOT NULL DEFAULT 'manual',
                added_at    TEXT    NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO autobrr_tracked (anilist_id, title, added_at) "
            "VALUES (1, 'Frieren', '2026-01-01T00:00:00')"
        )

    upgraded = make_storage(tmp_path)

    row = upgraded.list_autobrr_tracked()[0]
    assert row["title"] == "Frieren"
    assert row["status"] == ""
    assert row["status_at"] == ""
