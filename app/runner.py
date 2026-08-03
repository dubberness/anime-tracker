"""The tracking run itself: fetch, compare, persist."""

import threading
import traceback
from datetime import datetime

from clients import AniListClient, ShokoClient, SonarrClient
from clients import mappings as mapping_client
from core import compare
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

        # ---- compare ----
        self.state.set_phase("compare", "Matching against your library")
        results = compare.compare_collections(
            anilist, mapping_lookup, mal_ids, anidb_ids, settings.anilist
        )

        stats = stats_mod.build_stats(results)
        tiers = stats_mod.build_tiers(results, settings.ui.tiers)
        decades = stats_mod.build_decade_breakdown(results)
        genres = stats_mod.build_genre_breakdown(results)

        previous = self.storage.load_results() or {}
        diff = stats_mod.build_diff(results, previous.get("entries"))

        # ---- Sonarr ----
        sonarr_series = []
        sonarr_results = []
        sonarr_error = None

        if settings.sonarr.configured:
            self.state.set_phase("sonarr", "Reading the Sonarr library")
            try:
                sonarr_client = SonarrClient(settings.sonarr, network)
                sonarr_series = sonarr_client.fetch_series()
                sonarr_results = compare.compare_sonarr(sonarr_series, tvdb_ids)
            except Exception as exc:  # noqa: BLE001 - Sonarr is optional
                sonarr_error = str(exc)
                log.error("Sonarr fetch failed, continuing without it: %s", exc)
        else:
            log.info("Sonarr not configured - skipping the migration comparison")

        migration = stats_mod.build_migration_stats(sonarr_results)
        totals = stats_mod.build_library_totals(
            shoko_series, sonarr_series, shoko_episodes, episodes_suspect
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
            "sonarr_enabled": bool(settings.sonarr.configured),
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
