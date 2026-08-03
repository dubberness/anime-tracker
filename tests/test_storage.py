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
