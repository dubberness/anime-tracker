"""Route smoke tests and the settings API contract."""

import json

import pytest

import config as config_mod
from main import AppContext
from runner import Runner
from state import RunState
from storage import Storage
from web import create_app


@pytest.fixture
def ctx(runtime, clean_env):
    store = config_mod.ConfigStore(runtime)
    store.load()

    storage = Storage(runtime.database_file, runtime.results_file)
    state = RunState()
    runner = Runner(store, storage, state)

    return AppContext(runtime, store, storage, state, runner)


@pytest.fixture
def client(ctx):
    app = create_app(ctx)
    app.config["TESTING"] = True
    return app.test_client()


def configure(ctx):
    ctx.config.update({"shoko": {"url": "http://shoko:8111", "api_key": "k"}})


def seed_results(ctx):
    payload = {
        "generated_at": "2026-01-01T00:00:00",
        "entries": [{
            "rank": 1, "title": "Test Show", "score": 85, "popularity": 120000,
            "recommendation_score": 90.0, "episodes": 12, "year": 2015,
            "anilist_id": 1, "mal_id": "1", "anidb_id": "", "image": "",
            "owned": True, "is_franchise_root": True, "genres": [],
            "format": "TV", "status": "FINISHED",
            "tvdb_id": "500", "tvdb_season": 1, "sonarr_status": "owned",
        }],
        "sonarr": [],
        "stats": {"total": 1, "owned": 1, "missing": 0, "completion": 100.0,
                  "avg_owned_score": 85, "avg_missing_score": 0,
                  "missing_roots": 0, "owned_episodes": 12},
        "tiers": [{"tier": 100, "owned": 1, "total": 1, "completion": 100.0}],
        "decades": [], "genres": [],
        "diff": {"has_previous": False, "newly_owned": [], "newly_missing": [],
                 "newly_tracked": []},
        "migration": {"total": 0, "migrated": 0, "remaining": 0, "completion": 0,
                      "remaining_size_gb": 0, "migrated_size_gb": 0},
        "totals": {"shoko_shows": 1, "shoko_episodes": 12,
                   "shoko_episodes_suspect": False,
                   "sonarr_shows": 0, "sonarr_episodes": 0},
        "comparison": None,
        "seasons": [{
            "season": "SUMMER", "year": 2026, "label": "Summer 2026",
            "is_current": True,
            "sorts": {"popularity": [], "trending": [], "score": []},
        }],
        "sonarr_enabled": False, "sonarr_available": False,
        "sonarr_error": None,
    }
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()
    return payload


# ==========================
# Health & status
# ==========================

def test_health_is_always_available(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_health_reports_unconfigured_state(client):
    assert client.get("/api/health").get_json()["configured"] is False


def test_status_endpoint(client):
    body = client.get("/api/status").get_json()
    assert body["has_results"] is False
    assert body["run"]["running"] is False


# ==========================
# Pages
# ==========================

def test_dashboard_redirects_to_setup_when_unconfigured(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/settings" in response.headers["Location"]


def test_dashboard_shows_empty_state_before_the_first_run(client, ctx):
    configure(ctx)
    response = client.get("/")
    assert response.status_code == 200
    assert b"No results yet" in response.data


def test_dashboard_renders_results(client, ctx):
    configure(ctx)
    seed_results(ctx)

    response = client.get("/")
    assert response.status_code == 200
    assert b"100.0%" in response.data or b"100%" in response.data


def test_library_and_migration_pages_render(client, ctx):
    configure(ctx)
    seed_results(ctx)

    assert client.get("/library").status_code == 200
    assert client.get("/migration").status_code == 200


def test_seasons_page_renders_the_season_tabs(client, ctx):
    configure(ctx)
    seed_results(ctx)

    response = client.get("/seasons")
    assert response.status_code == 200
    assert b"Summer 2026" in response.data


def test_seasons_page_shows_an_empty_state_before_the_first_run(client, ctx):
    configure(ctx)
    response = client.get("/seasons")
    assert response.status_code == 200
    assert b"No results yet" in response.data


def test_seasons_page_survives_results_from_before_the_feature(client, ctx):
    """A results.json written by 4.0 has no seasons key at all."""
    configure(ctx)
    payload = seed_results(ctx)
    del payload["seasons"]
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()

    response = client.get("/seasons")
    assert response.status_code == 200
    assert b"collected on the next run" in response.data


def test_library_page_hides_the_sonarr_column_when_sonarr_is_off(client, ctx):
    configure(ctx)
    seed_results(ctx)

    body = client.get("/library").data
    assert b'data-sonarr=""' in body
    assert b"Sonarr only" not in body


def test_library_page_shows_the_sonarr_column_when_sonarr_is_on(client, ctx):
    configure(ctx)
    payload = seed_results(ctx)
    payload["sonarr_enabled"] = True
    payload["sonarr_available"] = True
    payload["comparison"] = {"both": 1, "shoko_only": 0, "sonarr_only": 0,
                             "neither": 0, "unmapped": 0, "comparable": 1,
                             "in_sonarr": 1}
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()

    body = client.get("/library").data
    assert b'data-sonarr="1"' in body
    assert b"Sonarr only" in body
    assert b"in both" in body


def test_library_page_warns_when_sonarr_was_unreachable(client, ctx):
    """A dead Sonarr must not read as an empty one."""
    configure(ctx)
    payload = seed_results(ctx)
    payload["sonarr_enabled"] = True
    payload["sonarr_available"] = False
    payload["sonarr_error"] = "Connection refused"
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()

    body = client.get("/library").data
    assert b"be reached on the last run" in body
    assert b"Connection refused" in body
    # The comparison filters need both libraries, so they stay hidden.
    assert b"Sonarr only" not in body


def test_settings_page_renders_when_unconfigured(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert b"Finish setting up" in response.data


def test_logs_page_renders(client):
    assert client.get("/logs").status_code == 200


def test_unknown_page_returns_404(client):
    assert client.get("/nope").status_code == 404


# ==========================
# Settings API
# ==========================

def test_settings_get_masks_secrets(client, ctx):
    ctx.config.update({"shoko": {"url": "http://shoko:8111", "api_key": "supersecret"}})

    body = client.get("/api/settings").get_json()
    assert body["settings"]["shoko"]["api_key"].startswith("****")
    assert "supersecret" not in json.dumps(body)


def test_settings_post_saves(client, ctx):
    response = client.post("/api/settings", json={
        "shoko": {"url": "http://shoko:8111", "api_key": "abc"},
    })
    assert response.status_code == 200
    assert ctx.config.settings.shoko.url == "http://shoko:8111"


def test_settings_post_rejects_invalid_cron(client):
    response = client.post("/api/settings", json={"schedule": {"cron": "nope"}})
    assert response.status_code == 400
    assert "cron" in response.get_json()["error"].lower()


def test_settings_post_requires_json(client):
    response = client.post("/api/settings", data="shoko.url=x")
    assert response.status_code == 415


def test_run_rejected_when_unconfigured(client):
    response = client.post("/api/run", json={})
    assert response.status_code == 400


def test_run_conflicts_when_already_running(client, ctx):
    configure(ctx)
    ctx.state.try_begin("test")

    response = client.post("/api/run", json={})
    assert response.status_code == 409


def test_results_endpoint_404s_without_results(client):
    assert client.get("/api/results").status_code == 404


def test_results_endpoint_returns_the_payload(client, ctx):
    seed_results(ctx)
    body = client.get("/api/results").get_json()
    assert body["stats"]["total"] == 1


def test_history_endpoint(client, ctx):
    run_id = ctx.storage.start_run()
    ctx.storage.finish_run(run_id, "success", stats={"completion": 25.0})

    body = client.get("/api/history").get_json()
    assert body["history"][0]["completion"] == 25.0


def test_logs_endpoint_returns_entries(client):
    body = client.get("/api/logs").get_json()
    assert isinstance(body["logs"], list)


def test_diagnostics_requires_shoko(client):
    response = client.get("/api/diagnostics/shoko")
    assert response.status_code == 400
