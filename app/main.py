"""Anime Collection Tracker - entrypoint.

Compares an AniList list against a Shoko library and tracks a Sonarr -> Shoko
migration, exposed as a small web app with a built-in scheduler.
"""

import signal
import sys
import threading

import logging_setup

logging_setup.configure()

import config as config_mod  # noqa: E402  - logging must be set up first
from logging_setup import get_logger  # noqa: E402
from runner import Runner  # noqa: E402
from scheduler import Scheduler  # noqa: E402
from state import RunState  # noqa: E402
from storage import Storage  # noqa: E402
from version import version_string  # noqa: E402

log = get_logger("main")


class AppContext:
    """Everything the web layer and scheduler need, wired together once."""

    def __init__(self, runtime, config, storage, state, runner, scheduler=None):
        self.runtime = runtime
        self.config = config
        self.storage = storage
        self.state = state
        self.runner = runner
        self.scheduler = scheduler


def build_context(argv=None):
    runtime = config_mod.load_runtime(argv if argv is not None else sys.argv[1:])

    config = config_mod.ConfigStore(runtime)
    settings = config.load()

    storage = Storage(runtime.database_file, runtime.results_file)
    state = RunState()
    runner = Runner(config, storage, state)
    runner.load_cached_results()

    ctx = AppContext(runtime, config, storage, state, runner)

    if not settings.is_configured:
        log.warning("=" * 62)
        log.warning("Shoko is not configured yet.")
        if runtime.serve_web:
            log.warning("Open http://<host>:%s/settings to finish setup.",
                        runtime.web_port)
        else:
            log.warning("Edit %s and restart.", runtime.config_file)
        log.warning("=" * 62)

    return ctx


def serve(ctx):
    """Start the WSGI server in a background thread."""
    from waitress import create_server

    from web import create_app

    app = create_app(ctx)
    server = create_server(
        app,
        host=ctx.runtime.web_host,
        port=ctx.runtime.web_port,
        threads=8,
        clear_untrusted_proxy_headers=True,
    )

    thread = threading.Thread(target=server.run, name="web", daemon=True)
    thread.start()

    log.info("Web interface listening on http://%s:%s",
             ctx.runtime.web_host, ctx.runtime.web_port)
    return server


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]

    log.info("=" * 62)
    log.info(" Anime Collection Tracker %s", version_string())
    log.info("=" * 62)

    ctx = build_context(argv)
    runtime = ctx.runtime

    # ---- one-shot mode ----
    if runtime.run_once:
        log.info("Running once (--once), then exiting")
        ok = ctx.runner.run(trigger="once")
        return 0 if ok else 1

    shutdown = threading.Event()

    def handle_signal(signum, _frame):
        log.info("Received signal %s - shutting down", signum)
        shutdown.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    server = None
    if runtime.serve_web:
        server = serve(ctx)
    else:
        log.info("Web interface disabled (SERVE_WEB=false)")

    scheduler = Scheduler(ctx.config, ctx.runner, ctx.state)
    ctx.scheduler = scheduler
    scheduler.start()

    if ctx.config.settings.schedule.run_on_start:
        if ctx.config.settings.is_configured:
            log.info("Running on start")
            ctx.runner.run_in_background(trigger="startup")
        else:
            log.info("Skipping the start-up run until Shoko is configured")

    shutdown.wait()

    scheduler.stop()
    if server is not None:
        try:
            server.close()
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass

    log.info("Goodbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
