"""Anime Collection Tracker - entrypoint.

Compares an AniList Top TV list against a Shoko library, and tracks a
Sonarr -> Shoko migration. Writes CSV exports and an HTML dashboard.
"""

import functools
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from croniter import croniter

from config import load_config
import clients
import compare
import report

VERSION = "3.0"


def run_once(cfg):
    """Execute one full tracking run."""
    started = datetime.now()

    print("")
    print("=" * 46)
    print(f" Run started: {started:%Y-%m-%d %H:%M:%S}")
    print("=" * 46)

    mappings = compare.load_mappings(cfg.mapping_file)
    anilist = clients.fetch_anilist(cfg)
    shoko_series = clients.fetch_shoko(cfg)

    mal_ids, anidb_ids, tvdb_ids = compare.extract_shoko_ids(shoko_series)

    results = compare.compare_collections(
        anilist, mappings, mal_ids, anidb_ids, cfg
    )

    stats = compare.build_stats(results)
    tiers = compare.build_tiers(results)
    diff = compare.build_diff(results, cfg.snapshot_file)

    sonarr_series = []
    sonarr_results = []

    if cfg.sonarr_enabled:
        try:
            sonarr_series = clients.fetch_sonarr(cfg)
            sonarr_results = compare.compare_sonarr(sonarr_series, tvdb_ids)
        except Exception as exc:  # noqa: BLE001
            print(f"Sonarr fetch failed, continuing without it: {exc}")
    else:
        print("Sonarr not configured - skipping migration comparison")

    migration = compare.build_migration_stats(sonarr_results)
    totals = compare.build_library_totals(shoko_series, sonarr_series)

    # ---- Console summary ----
    print("")
    print("=" * 30)
    print("Collection Progress")
    print("=" * 30)
    print(f"Tracked:     {stats['total']}")
    print(f"Owned:       {stats['owned']}")
    print(f"Missing:     {stats['missing']}")
    print(f"Completion:  {stats['completion']}%")
    print("")

    for tier in tiers:
        print(f"Top {tier['tier']}: {tier['completion']}% "
              f"({tier['owned']}/{tier['total']})")

    if diff.has_previous:
        print("")
        print(f"Since last run: {len(diff.newly_owned)} newly owned, "
              f"{len(diff.newly_tracked)} newly tracked")

    if cfg.sonarr_enabled and sonarr_results:
        print("")
        print("=" * 30)
        print("Sonarr Migration")
        print("=" * 30)
        print(f"Shoko library:  {totals['shoko_shows']} shows, "
              f"{totals['shoko_episodes']} episodes")
        print(f"Sonarr library: {totals['sonarr_shows']} shows, "
              f"{totals['sonarr_episodes']} episodes")
        print(f"Migrated:       {migration['migrated']}/{migration['total']} "
              f"({migration['completion']}%)")
        print(f"Remaining:      {migration['remaining']} "
              f"({migration['remaining_size_gb']} GB)")

    # ---- Outputs ----
    report.export_csvs(results, sonarr_results, cfg.output_dir)
    report.export_html(
        results, stats, tiers, diff, sonarr_results,
        migration, totals, cfg.output_dir,
        sonarr_enabled=cfg.sonarr_enabled and bool(sonarr_results),
    )

    compare.save_snapshot(results, cfg.snapshot_file)

    elapsed = (datetime.now() - started).total_seconds()
    print("")
    print(f"Run complete in {elapsed:.1f}s -> "
          f"{os.path.join(cfg.output_dir, 'report.html')}")


class QuietHandler(SimpleHTTPRequestHandler):
    """Static handler that doesn't spam the log with every asset request."""

    def log_message(self, fmt, *args):
        pass


def start_web_server(cfg):
    handler = functools.partial(QuietHandler, directory=cfg.output_dir)
    server = ThreadingHTTPServer(("0.0.0.0", cfg.web_port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"Dashboard served on port {cfg.web_port}")


def scheduler_loop(cfg):
    """Sleep until each scheduled time, then run."""
    if not croniter.is_valid(cfg.cron_schedule):
        print(f"Invalid CRON_SCHEDULE '{cfg.cron_schedule}' - "
              f"falling back to daily at 04:00")
        cfg.cron_schedule = "0 4 * * *"

    while True:
        now = datetime.now()
        next_run = croniter(cfg.cron_schedule, now).get_next(datetime)
        wait = max((next_run - now).total_seconds(), 1)

        print(f"Next run: {next_run:%Y-%m-%d %H:%M:%S} "
              f"({wait / 3600:.1f}h away)")

        time.sleep(wait)

        try:
            run_once(cfg)
        except Exception:  # noqa: BLE001 - a failed run must not kill the loop
            print("Scheduled run failed:")
            traceback.print_exc()


def main():
    print("=" * 46)
    print(f" Anime Collection Tracker v{VERSION}")
    print("=" * 46)

    cfg = load_config()

    once = "--once" in sys.argv or os.environ.get("RUN_ONCE", "").lower() in (
        "1", "true", "yes"
    )

    if cfg.serve_web and not once:
        start_web_server(cfg)

    if cfg.run_on_start or once:
        try:
            run_once(cfg)
        except Exception:  # noqa: BLE001
            print("Initial run failed:")
            traceback.print_exc()
            if once:
                sys.exit(1)
            print("Continuing to scheduled runs.")

    if once:
        return

    scheduler_loop(cfg)


if __name__ == "__main__":
    main()
