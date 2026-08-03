"""The tracking run itself: fetch, compare, persist."""

import threading
import time
import traceback
from datetime import date, datetime

from clients import AniListClient, ShokoClient, SonarrClient
from clients import mappings as mapping_client
from core import compare
from core import seasons as season_mod
from core import stats as stats_mod
from logging_setup import get_logger

log = get_logger(__name__)


class Runner:
    """Owns one tracking run at a time and the results it produces."""

    def __init__(self, config_store, storage, run_state):
        self.config = config_store
        self.storage = storage
        self.state = run_state
        self._results_lock = threading.Lock()
        self._results = None

    # ==========================
    # Results access
    # ==========================

    def load_cached_results(self):
        """Pull the last run's results off disk at startup."""
        payload = self.storage.load_results()
        if payload:
            with self._results_lock:
                self._results = payload
            log.info("Loaded results from the previous run (%s entries)",
                     len(payload.get("entries", [])))
        return payload

    @property
    def results(self):
        with self._results_lock:
            return self._results

    def _store_results(self, payload):
        with self._results_lock:
            self._results = payload
        self.storage.save_results(payload)

    # ==========================
    # Run
    # ==========================

    def run_in_background(self, trigger="manual"):
        """Kick off a run in a worker thread. False if one is already going."""
        if not self.state.try_begin(trigger):
            return False

        thread = threading.Thread(
            target=self._run_guarded,
            kwargs={"already_claimed": True, "trigger": trigger},
            name="tracker-run",
            daemon=True,
        )
        thread.start()
        return True

    def run(self, trigger="manual"):
        """Run synchronously. False if a run is already in flight."""
        if not self.state.try_begin(trigger):
            log.warning("A run is already in progress - skipping this %s trigger", trigger)
            return False
        return self._run_guarded(already_claimed=True, trigger=trigger)

    def _run_guarded(self, already_claimed=False, trigger="manual"):
        if not already_claimed and not self.state.try_begin(trigger):
            return False

        run_id = self.storage.start_run()
        started = datetime.now()
        error = None

        try:
            self._execute(run_id, started)
            return True
        except Exception as exc:  # noqa: BLE001 - a failed run must not kill the process
            error = str(exc)
            log.error("Run failed: %s", exc)
            log.debug("%s", traceback.format_exc())
            self.storage.finish_run(
                run_id, "failed", error=error,
                duration=(datetime.now() - started).total_seconds(),
            )
            return False
        finally:
            self.state.finish(error=error)
            self.storage.prune()

    def _build_seasons(self, client, settings, mappings, mal_ids, anidb_ids,
                       sonarr_index, sonarr_available, previous_seasons):
        """Ranked charts for the current season and the one either side.

        A failure here keeps the previous run's charts rather than blanking the
        page: the seasonal view is a bonus on top of the run, not its point,
        and an empty table reads as "nothing airing" rather than "fetch failed".
        """
        limit = settings.ui.season_limit
        today = date.today()
        current = (season_mod.season_of(today), today.year)
        blocks = []

        for index, (season, year) in enumerate(season_mod.window(today)):
            if index:
                time.sleep(settings.anilist.request_delay_ms / 1000)

            try:
                ranked = client.fetch_season(season, year, per_page=limit)
            except Exception as exc:  # noqa: BLE001 - seasonal data is optional
                log.error("Seasonal fetch failed for %s %s (%s) - keeping the "
                          "previous charts", season, year, exc)
                return previous_seasons or []

            blocks.append({
                "season": season,
                "year": year,
                "label": season_mod.label(season, year),
                "is_current": (season, year) == current,
                "sorts": {
                    key: [
                        entry.to_dict() for entry in compare.build_season_entries(
                            media, mappings, mal_ids, anidb_ids,
                            sonarr_index, sonarr_available, limit,
                        )
                    ]
                    for key, media in ranked.items()
                },
            })

        log.info("Seasonal charts built for %s",
                 ", ".join(block["label"] for block in blocks))
        return blocks

    def _execute(self, run_id, started):
        settings = self.config.settings

        if not settings.is_configured:
            raise RuntimeError(
                "Shoko is not configured yet - open Settings and add the URL and API key"
            )

        network = settings.network

        # ---- mappings ----
        self.state.set_phase("mappings", "Loading anime ID mappings")
        mapping_lookup = mapping_client.load(settings.mappings)

        # ---- AniList ----
        self.state.set_phase("anilist", "Fetching the AniList list")
        anilist_client = AniListClient(
            settings.anilist, self.config.runtime.cache_file, network
        )
        anilist = anilist_client.fetch(
            progress=lambda msg: self.state.set_message(msg)
        )

        # ---- Shoko ----
        self.state.set_phase("shoko", "Reading the Shoko library")
        shoko_client = ShokoClient(settings.shoko, network)
        shoko_series = shoko_client.fetch_series(
            progress=lambda msg: self.state.set_message(msg)
        )

        mal_ids, anidb_ids, tvdb_ids = compare.extract_shoko_ids(shoko_series)
        shoko_episodes, episodes_suspect = compare.count_shoko_episodes(shoko_series)

        # ---- Sonarr ----
        # Ahead of the comparison so every tracked entry can carry its Sonarr
        # status, not just the migration table.
        sonarr_series = []
        sonarr_results = []
        sonarr_index = {}
        sonarr_error = None
        sonarr_enabled = bool(settings.sonarr.configured)

        if sonarr_enabled:
            self.state.set_phase("sonarr", "Reading the Sonarr library")
            try:
                sonarr_client = SonarrClient(settings.sonarr, network)
                sonarr_series = sonarr_client.fetch_series()
                sonarr_index = compare.build_sonarr_index(sonarr_series)
                sonarr_results = compare.compare_sonarr(sonarr_series, tvdb_ids)
            except Exception as exc:  # noqa: BLE001 - Sonarr is optional
                sonarr_error = str(exc)
                log.error("Sonarr fetch failed, continuing without it: %s", exc)
        else:
            log.info("Sonarr not configured - skipping the Sonarr comparison")

        # Configured but unreachable has to read as "unknown" rather than
        # "missing", or a dead Sonarr looks like an empty one.
        sonarr_available = sonarr_enabled and sonarr_error is None

        # ---- compare ----
        self.state.set_phase("compare", "Matching against your library")
        results = compare.compare_collections(
            anilist, mapping_lookup, mal_ids, anidb_ids, settings.anilist,
            sonarr_index, sonarr_available,
        )

        stats = stats_mod.build_stats(results)
        tiers = stats_mod.build_tiers(results, settings.ui.tiers)
        decades = stats_mod.build_decade_breakdown(results)
        genres = stats_mod.build_genre_breakdown(results)
        comparison = stats_mod.build_comparison(results, sonarr_available)

        previous = self.storage.load_results() or {}
        diff = stats_mod.build_diff(results, previous.get("entries"))

        migration = stats_mod.build_migration_stats(sonarr_results)
        totals = stats_mod.build_library_totals(
            shoko_series, sonarr_series, shoko_episodes, episodes_suspect
        )

        # ---- seasons ----
        self.state.set_phase("seasons", "Fetching the seasonal charts")
        seasons = self._build_seasons(
            anilist_client, settings, mapping_lookup, mal_ids, anidb_ids,
            sonarr_index, sonarr_available, previous.get("seasons"),
        )

        # ---- persist ----
        self.state.set_phase("persist", "Saving results")
        duration = (datetime.now() - started).total_seconds()

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(duration, 1),
            "entries": [entry.to_dict() for entry in results],
            "sonarr": [entry.to_dict() for entry in sonarr_results],
            "stats": stats,
            "tiers": tiers,
            "decades": decades,
            "genres": genres,
            "diff": diff.to_dict(),
            "migration": migration,
            "totals": totals,
            "comparison": comparison,
            "seasons": seasons,
            "sonarr_enabled": sonarr_enabled,
            "sonarr_available": sonarr_available,
            "sonarr_error": sonarr_error,
        }

        self._store_results(payload)
        self.storage.finish_run(
            run_id, "success", stats=stats, totals=totals,
            migration=migration, duration=duration,
        )

        log.info(
            "Run complete in %.1fs - %s/%s owned (%s%%)",
            duration, stats["owned"], stats["total"], stats["completion"],
        )
