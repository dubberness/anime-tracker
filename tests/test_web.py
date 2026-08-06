"""Route smoke tests and the settings API contract."""

import json
import re

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
            "sorts": {
                "popularity": [
                    {"anilist_id": 10, "title": "Airing One", "title_alt": "",
                     "owned": False, "mal_id": "10", "anidb_id": "",
                     "rank": 1, "score": 80, "popularity": 5000, "image": "",
                     "is_franchise_root": True, "sonarr_status": "unknown"},
                    {"anilist_id": 11, "title": "Airing Two", "title_alt": "",
                     "owned": False, "mal_id": "11", "anidb_id": "",
                     "rank": 2, "score": 75, "popularity": 4000, "image": "",
                     "is_franchise_root": True, "sonarr_status": "unknown"},
                ],
                "trending": [], "score": [],
            },
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


def test_migration_page_survives_results_from_before_the_shoko_view(client, ctx):
    """A results.json written before this feature has no shoko key, and its
    migration block has none of the new counts. Jinja raises on attribute
    access through a missing key, so this is a 500 rather than a blank."""
    configure(ctx)
    payload = seed_results(ctx)
    payload["sonarr_enabled"] = True
    payload.pop("shoko", None)
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()

    response = client.get("/migration")
    assert response.status_code == 200
    assert b"Only in Shoko" not in response.data


def sonarr_row(title, tvdb_id, **kwargs):
    row = {
        "title": title, "tvdb_id": tvdb_id, "status": "ended",
        "episode_file_count": 50, "episode_count": 50, "size_gb": 12.0,
        "migrated": False, "shoko_episodes": 0, "partial": False,
        "unmappable": False,
    }
    row.update(kwargs)
    return row


def test_migration_page_separates_series_it_cannot_check(client, ctx):
    configure(ctx)
    payload = seed_results(ctx)
    payload["sonarr_enabled"] = True
    payload["sonarr"] = [
        sonarr_row("Digimon Adventure 02", 459436, unmappable=True),
        sonarr_row("Genuinely Missing", 100),
    ]
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()

    body = client.get("/migration").data.decode()
    unchecked = body.index("Can't be checked")
    still_only = body.index("Still only in Sonarr")

    assert "Digimon Adventure 02" in body
    # The unmappable one must not also sit in the work list above it.
    assert body.index("Digimon Adventure 02") > unchecked
    assert still_only < unchecked
    assert body.count("Digimon Adventure 02") == 1


def test_migration_page_survives_rows_from_before_the_unmappable_flag(client, ctx):
    """Old rows have no unmappable key; rejectattr must treat that as false
    rather than dropping every row out of the work list."""
    configure(ctx)
    payload = seed_results(ctx)
    payload["sonarr_enabled"] = True
    old_row = sonarr_row("Old Shape Show", 100)
    del old_row["unmappable"]
    del old_row["partial"]
    payload["sonarr"] = [old_row]
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()

    body = client.get("/migration").data.decode()
    assert "Old Shape Show" in body
    assert "Can't be checked" not in body


def test_migration_page_lists_what_is_only_in_shoko(client, ctx):
    configure(ctx)
    payload = seed_results(ctx)
    payload["sonarr_enabled"] = True
    payload["shoko"] = [{
        "title": "Shoko Only Show", "anidb_id": "42", "tvdb_ids": ["900"],
        "tvdb_id": "900", "episodes": 13, "sonarr_status": "missing",
        "sonarr_episodes": 0,
    }]
    payload["migration"].update({
        "shoko_total": 1, "shoko_only": 1, "shoko_only_episodes": 13,
        "shoko_unmapped": 0, "partial": 0, "partial_missing_episodes": 0,
    })
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()

    response = client.get("/migration")
    assert response.status_code == 200
    assert b"Only in Shoko" in response.data
    assert b"Shoko Only Show" in response.data


def test_migration_page_warns_instead_of_reporting_zero_when_sonarr_is_down(client, ctx):
    """A dead Sonarr used to render as a confident "0 of 0 - 0%"."""
    configure(ctx)
    payload = seed_results(ctx)
    payload["sonarr_enabled"] = True
    payload["sonarr_error"] = "Connection refused"
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()

    response = client.get("/migration")
    assert response.status_code == 200
    assert b"Connection refused" in response.data
    assert b"0%" not in response.data


def test_the_migration_trend_skips_runs_where_sonarr_was_unreachable(ctx):
    """Those runs recorded zero series; plotting them invents a crash to 0%."""
    from web.server import _migration_trend_points

    points = _migration_trend_points([
        {"started_at": "2026-01-01T00:00:00", "sonarr_shows": 10,
         "sonarr_migrated": 5},
        {"started_at": "2026-01-02T00:00:00", "sonarr_shows": 0,
         "sonarr_migrated": 0},
        {"started_at": "2026-01-03T00:00:00", "sonarr_shows": 10,
         "sonarr_migrated": 8},
    ])

    assert [p["value"] for p in points] == [50.0, 80.0]


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


# ==========================
# Autobrr
# ==========================

def test_list_is_plaintext_and_empty_before_anything_is_tracked(client):
    """autobrr polls this on a schedule - nothing tracked is not an error."""
    response = client.get("/api/autobrr/list")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.data == b""


def test_tracking_a_show_puts_it_on_the_list(client, ctx):
    response = client.post("/api/autobrr/track", json={
        "anilist_id": 55, "title": "Frieren", "title_alt": "Sousou no Frieren",
        "mal_id": "52991", "anidb_id": "17617",
    })
    assert response.status_code == 200

    body = client.get("/api/autobrr/list").data.decode()
    assert body.split("\n")[:2] == ["Frieren", "Sousou no Frieren"]
    assert ctx.storage.autobrr_tracked_ids() == {55}


def test_tracking_requires_an_id_and_title(client):
    assert client.post("/api/autobrr/track", json={"title": "x"}).status_code == 400
    assert client.post("/api/autobrr/track", json={"anilist_id": 1}).status_code == 400


def test_untracking_removes_it_from_the_list(client, ctx):
    ctx.storage.track_autobrr(55, "Frieren")

    response = client.delete("/api/autobrr/track/55")
    assert response.status_code == 200
    assert response.get_json()["tracked"] is False
    assert client.get("/api/autobrr/list").data == b""


def test_untracking_an_auto_pick_excludes_it(client, ctx):
    """Otherwise the next run would silently re-add it."""
    configure(ctx)
    seed_results(ctx)
    ctx.storage.track_autobrr(10, "Airing One", source="auto")

    response = client.delete("/api/autobrr/track/10")

    assert response.get_json()["excluded"] is True
    assert ctx.storage.autobrr_excluded_ids() == {10}


def test_untracking_an_upcoming_season_auto_pick_excludes_it(client, ctx):
    """The drift guard: the run seeds from next season too, so the web layer
    has to agree that those are auto-picks or the next run re-adds them."""
    configure(ctx)
    payload = seed_results(ctx)
    payload["seasons"].append({
        "season": "FALL", "year": 2026, "label": "Fall 2026",
        "is_current": False, "is_upcoming": True,
        "sorts": {
            "popularity": [
                {"anilist_id": 20, "title": "Next Season Sequel", "title_alt": "",
                 "owned": False, "sequel_of_owned": True, "mal_id": "20",
                 "anidb_id": "", "rank": 1, "score": 80, "popularity": 3000,
                 "image": "", "is_franchise_root": False,
                 "sonarr_status": "unknown"},
            ],
            "trending": [], "score": [],
        },
    })
    ctx.storage.save_results(payload)
    ctx.runner.load_cached_results()
    ctx.storage.track_autobrr(20, "Next Season Sequel", source="auto")

    response = client.delete("/api/autobrr/track/20")

    assert response.get_json()["excluded"] is True
    assert ctx.storage.autobrr_excluded_ids() == {20}


def test_untracking_a_manual_pick_does_not_exclude_it(client, ctx):
    """Auto-seeding was never going to touch it, so there is nothing to block."""
    configure(ctx)
    seed_results(ctx)
    ctx.storage.track_autobrr(999, "Something Obscure")

    response = client.delete("/api/autobrr/track/999")

    assert response.get_json()["excluded"] is False
    assert ctx.storage.autobrr_excluded_ids() == set()


def test_seasons_page_marks_tracked_shows(client, ctx):
    """Rendered server-side so the button state is right on first paint."""
    configure(ctx)
    seed_results(ctx)
    ctx.storage.track_autobrr(10, "Airing One")

    body = client.get("/seasons").data.decode()
    embedded = re.search(r'id="season-data">(.*?)</script>', body, re.S).group(1)
    entries = json.loads(embedded)[0]["sorts"]["popularity"]

    tracked = {e["anilist_id"]: e["autobrr_tracked"] for e in entries}
    assert tracked == {10: True, 11: False}


def test_settings_page_shows_the_list_url(client, ctx):
    body = client.get("/settings").data.decode()
    assert "/api/autobrr/list" in body


def test_settings_accepts_autobrr_and_masks_the_key(client, ctx):
    response = client.post("/api/settings", json={
        "autobrr": {"url": "http://autobrr:7474", "api_key": "secret",
                    "list_id": "3", "auto_seed_limit": 5},
    })
    assert response.status_code == 200
    assert ctx.config.settings.autobrr.url == "http://autobrr:7474"
    assert ctx.config.settings.autobrr.configured is True

    body = client.get("/api/settings").get_json()
    assert body["settings"]["autobrr"]["api_key"].startswith("****")
    assert "secret" not in json.dumps(body)


def test_settings_rejects_a_bad_autobrr_url(client):
    response = client.post("/api/settings", json={"autobrr": {"url": "autobrr:7474"}})
    assert response.status_code == 400
    assert "autobrr" in response.get_json()["error"].lower()


def test_settings_rejects_an_out_of_range_seed_limit(client):
    response = client.post("/api/settings", json={"autobrr": {"auto_seed_limit": 99}})
    assert response.status_code == 400
