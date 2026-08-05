"""Flask application: pages, JSON API and the settings form."""

import os
from datetime import datetime

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

import config as config_mod
import logging_setup
from clients import (
    AniListClient,
    AutobrrClient,
    ShokoClient,
    SonarrClient,
    describe_error,
)
from clients import mappings as mapping_client
from core import autobrr as autobrr_mod
from logging_setup import get_logger
from version import VERSION, version_string

log = get_logger(__name__)


def create_app(ctx):
    """Build the Flask app. `ctx` is the AppContext wired up in main."""
    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
    )
    app.config["JSON_SORT_KEYS"] = False
    app.config["ctx"] = ctx

    _register_pages(app, ctx)
    _register_api(app, ctx)

    @app.context_processor
    def inject_globals():
        return {
            "version": version_string(),
            "app_version": VERSION,
            "settings": ctx.config.settings,
            "is_configured": ctx.config.settings.is_configured,
            "now": datetime.now(),
        }

    @app.errorhandler(404)
    def not_found(_):
        return render_template("error.html", code=404,
                               message="That page doesn't exist."), 404

    @app.errorhandler(500)
    def server_error(exc):
        log.error("Unhandled error: %s", exc)
        return render_template("error.html", code=500,
                               message="Something went wrong - check the logs."), 500

    return app


# ==========================
# Pages
# ==========================

def _register_pages(app, ctx):

    @app.route("/")
    def dashboard():
        results = ctx.runner.results

        if not ctx.config.settings.is_configured:
            return redirect(url_for("settings_page", setup=1))

        if not results:
            return render_template(
                "empty.html",
                status=ctx.state.snapshot(),
                title="Dashboard",
            )

        history = ctx.storage.history(ctx.config.settings.ui.history_points)

        return render_template(
            "dashboard.html",
            title="Dashboard",
            data=results,
            stats=results["stats"],
            tiers=results["tiers"],
            diff=results["diff"],
            history=history,
            trend_points=_trend_points(history),
            decades=results.get("decades", []),
            genres=results.get("genres", []),
            top_picks=_top_picks(results, 6),
            status=ctx.state.snapshot(),
        )

    @app.route("/library")
    def library():
        results = ctx.runner.results
        if not results:
            return render_template("empty.html", status=ctx.state.snapshot(),
                                   title="Library")

        return render_template(
            "library.html",
            title="Library",
            data=results,
            stats=results["stats"],
            comparison=results.get("comparison"),
            status=ctx.state.snapshot(),
        )

    @app.route("/seasons")
    def seasons_page():
        results = ctx.runner.results
        if not results:
            return render_template("empty.html", status=ctx.state.snapshot(),
                                   title="Seasons")

        seasons = _annotate_tracked(results.get("seasons") or [],
                                    ctx.storage.autobrr_tracked_ids())

        return render_template(
            "seasons.html",
            title="Seasons",
            data=results,
            seasons=seasons,
            tracked_count=len(ctx.storage.autobrr_tracked_ids()),
            status=ctx.state.snapshot(),
        )

    @app.route("/migration")
    def migration():
        results = ctx.runner.results
        if not results:
            return render_template("empty.html", status=ctx.state.snapshot(),
                                   title="Migration")

        return render_template(
            "migration.html",
            title="Migration",
            data=results,
            migration=results.get("migration", {}),
            totals=results.get("totals", {}),
            sonarr=results.get("sonarr", []),
            shoko=results.get("shoko", []),
            trend=_migration_trend_points(
                ctx.storage.history(ctx.config.settings.ui.history_points)
            ),
            status=ctx.state.snapshot(),
        )

    @app.route("/settings")
    def settings_page():
        return render_template(
            "settings.html",
            title="Settings",
            values=ctx.config.settings.redacted(),
            env_locked=ctx.config.env_locked,
            valid_formats=config_mod.VALID_FORMATS,
            valid_sorts=config_mod.VALID_SORTS,
            runtime=ctx.runtime,
            setup=request.args.get("setup") == "1",
            autobrr_list_url=url_for("api_autobrr_list", _external=True),
            tracked_count=len(ctx.storage.autobrr_tracked_ids()),
            status=ctx.state.snapshot(),
        )

    @app.route("/logs")
    def logs_page():
        return render_template("logs.html", title="Logs",
                               status=ctx.state.snapshot())


def _trend_points(history):
    """Shape run history for the completion-over-time chart."""
    points = []

    for row in history:
        started = row.get("started_at") or ""
        try:
            label = datetime.fromisoformat(started).strftime("%d %b %H:%M")
        except (ValueError, TypeError):
            label = started

        points.append({
            "label": label,
            "value": float(row.get("completion") or 0),
            "detail": f"{row.get('owned', 0)} of {row.get('tracked', 0)} owned",
        })

    return points


def _migration_trend_points(history):
    """Shape run history for the migration-over-time chart.

    Runs where Sonarr was unreachable still wrote a history row, and it
    recorded zero series - see Storage.finish_run. Plotting those would show
    the migration collapsing to 0% and recovering, so they are skipped rather
    than drawn as real data points.
    """
    points = []

    for row in history:
        total = row.get("sonarr_shows") or 0
        if not total:
            continue

        started = row.get("started_at") or ""
        try:
            label = datetime.fromisoformat(started).strftime("%d %b %H:%M")
        except (ValueError, TypeError):
            label = started

        migrated = row.get("sonarr_migrated") or 0
        points.append({
            "label": label,
            "value": round(migrated / total * 100, 2),
            "detail": f"{migrated} of {total} in Shoko",
        })

    return points


def _auto_seed_ids(ctx):
    """AniList IDs the next run would auto-track, from the last run's data.

    Must stay in step with Runner._auto_seed_tracked - this is what decides
    whether untracking a show also records an exclusion, so if the two ever
    disagreed the next run would quietly re-add it.
    """
    results = ctx.runner.results or {}

    candidates = autobrr_mod.auto_seed_candidates(
        results.get("seasons") or [],
        ctx.storage.autobrr_excluded_ids(),
        ctx.config.settings.autobrr.auto_seed_limit,
    )
    return {entry["anilist_id"] for entry in candidates}


def _annotate_tracked(seasons, tracked_ids):
    """Mark which season entries autobrr is already being told to grab.

    Done server-side so the page renders the right button state immediately
    instead of flickering through a second request.
    """
    annotated = []

    for block in seasons:
        sorts = {
            key: [dict(entry, autobrr_tracked=entry["anilist_id"] in tracked_ids)
                  for entry in entries]
            for key, entries in (block.get("sorts") or {}).items()
        }
        annotated.append(dict(block, sorts=sorts))

    return annotated


def _top_picks(results, limit):
    missing_roots = [
        e for e in results["entries"]
        if not e["owned"] and e["is_franchise_root"]
    ]
    missing_roots.sort(key=lambda e: e["recommendation_score"], reverse=True)
    return missing_roots[:limit]


# ==========================
# JSON API
# ==========================

def _require_json(handler):
    """Reject form-encoded posts - a cheap same-origin guard for mutations."""
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify({"ok": False, "error": "JSON body required"}), 415
        return handler(*args, **kwargs)
    wrapper.__name__ = handler.__name__
    return wrapper


def _register_api(app, ctx):

    @app.route("/api/health")
    def health():
        """Liveness only - deliberately cheap, used by the Docker healthcheck."""
        return jsonify({
            "status": "ok",
            "version": VERSION,
            "configured": ctx.config.settings.is_configured,
            "running": ctx.state.is_running,
        })

    @app.route("/api/status")
    def status():
        results = ctx.runner.results
        return jsonify({
            "run": ctx.state.snapshot(),
            "has_results": bool(results),
            "generated_at": (results or {}).get("generated_at"),
            "stats": (results or {}).get("stats"),
            "configured": ctx.config.settings.is_configured,
        })

    @app.route("/api/results")
    def api_results():
        results = ctx.runner.results
        if not results:
            return jsonify({"ok": False, "error": "No results yet"}), 404
        return jsonify(results)

    @app.route("/api/history")
    def api_history():
        limit = request.args.get("limit", type=int) or ctx.config.settings.ui.history_points
        return jsonify({
            "history": ctx.storage.history(limit),
            "runs": ctx.storage.recent_runs(10),
        })

    @app.route("/api/logs")
    def api_logs():
        limit = request.args.get("limit", type=int) or 200
        return jsonify({"logs": logging_setup.ring_handler.tail(limit)})

    @app.route("/api/run", methods=["POST"])
    def api_run():
        if ctx.state.is_running:
            return jsonify({"ok": False, "error": "A run is already in progress"}), 409

        if not ctx.config.settings.is_configured:
            return jsonify({
                "ok": False,
                "error": "Shoko is not configured yet",
            }), 400

        started = ctx.runner.run_in_background(trigger="manual")
        if not started:
            return jsonify({"ok": False, "error": "A run is already in progress"}), 409

        return jsonify({"ok": True, "message": "Run started"})

    @app.route("/api/settings", methods=["GET"])
    def api_settings_get():
        return jsonify({
            "settings": ctx.config.settings.redacted(),
            "env_locked": ctx.config.env_locked,
        })

    @app.route("/api/settings", methods=["POST"])
    @_require_json
    def api_settings_post():
        payload = request.get_json(silent=True) or {}

        try:
            ctx.config.update(payload)
        except config_mod.ValidationError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to save settings: %s", exc)
            return jsonify({"ok": False, "error": f"Could not save: {exc}"}), 500

        # Let the scheduler notice a changed cron immediately.
        if ctx.scheduler:
            ctx.scheduler.wake()

        return jsonify({
            "ok": True,
            "settings": ctx.config.settings.redacted(),
            "env_locked": ctx.config.env_locked,
        })

    @app.route("/api/test/<service>", methods=["POST"])
    @_require_json
    def api_test(service):
        """Test a connection using submitted values, falling back to saved ones."""
        payload = request.get_json(silent=True) or {}
        settings = ctx.config.settings
        network = settings.network

        try:
            if service == "shoko":
                candidate = _merge_connection(settings.shoko, payload)
                message = ShokoClient(candidate, network).test_connection()

            elif service == "sonarr":
                candidate = _merge_connection(settings.sonarr, payload)
                message = SonarrClient(candidate, network).test_connection()

            elif service == "autobrr":
                candidate = _merge_connection(settings.autobrr, payload)
                message = AutobrrClient(candidate, network).test_connection()

            elif service == "anilist":
                message = AniListClient(
                    settings.anilist, ctx.runtime.cache_file, network
                ).test_connection()

            elif service == "mappings":
                lookup = mapping_client.load(settings.mappings)
                message = f"{len(lookup)} AniList IDs available"

            else:
                return jsonify({"ok": False, "error": "Unknown service"}), 404

            return jsonify({"ok": True, "message": message})

        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            return jsonify({"ok": False, "error": describe_error(exc)})

    @app.route("/api/diagnostics/shoko")
    def api_shoko_diagnostics():
        """Show how IDs actually look on this Shoko instance.

        The ID paths are version-dependent, so this reports what was found
        rather than making the user dig through raw API output.
        """
        settings = ctx.config.settings
        if not settings.shoko_configured:
            return jsonify({"ok": False, "error": "Shoko is not configured"}), 400

        try:
            sample = ShokoClient(settings.shoko, settings.network).sample_series()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": describe_error(exc)})

        if not sample:
            return jsonify({"ok": False, "error": "Shoko returned no series"})

        from core.compare import count_shoko_episodes, extract_shoko_ids

        mal, anidb, tvdb = extract_shoko_ids([sample])
        episodes, suspect = count_shoko_episodes([sample])

        return jsonify({
            "ok": True,
            "series_name": sample.get("Name") or sample.get("Title") or "?",
            "id_keys": sorted((sample.get("IDs") or {}).keys()),
            "top_level_keys": sorted(sample.keys()),
            "found": {
                "mal": sorted(mal),
                "anidb": sorted(anidb),
                "tvdb": sorted(tvdb),
            },
            "episodes": episodes,
            "episodes_suspect": suspect,
        })

    @app.route("/api/autobrr/list")
    def api_autobrr_list():
        """The plaintext list autobrr polls. This is the URL to paste into it.

        Deliberately unauthenticated and always 200: autobrr fetches it on a
        schedule, and an empty collection is a normal state rather than an
        error.
        """
        body = autobrr_mod.build_list_text(ctx.storage.list_autobrr_tracked())
        return Response(body + "\n" if body else "", mimetype="text/plain")

    @app.route("/api/autobrr/tracked")
    def api_autobrr_tracked():
        return jsonify({"tracked": ctx.storage.list_autobrr_tracked()})

    @app.route("/api/autobrr/track", methods=["POST"])
    @_require_json
    def api_autobrr_track():
        payload = request.get_json(silent=True) or {}

        try:
            anilist_id = int(payload["anilist_id"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "A numeric anilist_id is required"}), 400

        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"ok": False, "error": "A title is required"}), 400

        ctx.storage.track_autobrr(
            anilist_id,
            title,
            (payload.get("title_alt") or "").strip(),
            payload.get("mal_id") or "",
            payload.get("anidb_id") or "",
            autobrr_mod.MANUAL,
        )

        return jsonify({"ok": True, "tracked": True})

    @app.route("/api/autobrr/track/<int:anilist_id>", methods=["DELETE"])
    def api_autobrr_untrack(anilist_id):
        # Untracking something the next run would auto-add again has to stick,
        # otherwise the decision silently reverses a few hours later.
        exclude = anilist_id in _auto_seed_ids(ctx)
        ctx.storage.untrack_autobrr(anilist_id, exclude=exclude)

        return jsonify({"ok": True, "tracked": False, "excluded": exclude})

    @app.route("/api/mappings/refresh", methods=["POST"])
    @_require_json
    def api_refresh_mappings():
        try:
            lookup = mapping_client.load(
                ctx.config.settings.mappings, force_download=True
            )
            return jsonify({
                "ok": True,
                "message": f"Downloaded - {len(lookup)} AniList IDs available",
            })
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)})

    @app.route("/api/cache", methods=["DELETE"])
    def api_clear_cache():
        path = ctx.runtime.cache_file
        if os.path.exists(path):
            os.remove(path)
            return jsonify({"ok": True, "message": "AniList cache cleared"})
        return jsonify({"ok": True, "message": "Cache was already empty"})


def _merge_connection(current, payload):
    """Copy `current`, overlaying a submitted url/api_key if they're real."""
    import copy

    candidate = copy.deepcopy(current)

    url = (payload.get("url") or "").strip()
    if url:
        candidate.url = url.rstrip("/")

    api_key = payload.get("api_key") or ""
    if api_key and not api_key.startswith("****"):
        candidate.api_key = api_key

    return candidate
