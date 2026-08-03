"""Flask application: pages, JSON API and the settings form."""

import os
from datetime import datetime

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

import config as config_mod
import logging_setup
from clients import AniListClient, ShokoClient, SonarrClient, describe_error
from clients import mappings as mapping_client
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
            migration=results["migration"],
            totals=results["totals"],
            sonarr=results.get("sonarr", []),
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
