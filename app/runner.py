"""The tracking run itself: fetch, compare, persist."""

import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime

from clients import AniListClient, AutobrrClient, ShokoClient, SonarrClient
from clients import mappings as mapping_client
from core import autobrr as autobrr_mod
from core import compare
from core import seasons as season_mod
from core import stats as stats_mod
from logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class LookupContext:
    """Everything build_season_entries needs, kept alive between runs.

    The season picker fetches one season at request time and has to match it
    against Shoko and Sonarr right there, but those libraries are only read
    during a run. Holding the last run's lookups costs about 25MB - nearly all
    of it the mapping file, which used to be parsed and thrown away every run
    anyway - and avoids both bloating results.json with data the browser never
    reads and hammering Shoko from a web worker thread.

    Never mutated after construction, which is what makes it safe to hand the
    same instance to all eight waitress threads.
    """

    mappings: dict = field(default_factory=dict)
    mal_ids: set = field(default_factory=set)
    anidb_ids: set = field(default_factory=set)
    local_by_mal: dict = field(default_factory=dict)
    local_by_anidb: dict = field(default_factory=dict)
    sonarr_index: dict = field(default_factory=dict)
    sonarr_available: bool = False
    built_at: datetime = None


class Runner:
    """Owns one tracking run at a time and the results it produces."""

    def __init__(self, config_store, storage, run_state):
        self.config = config_store
        self.storage = storage
        self.state = run_state
        self._results_lock = threading.Lock()
        self._results = None
        self._lookups_lock = threading.Lock()
        self._lookups = None

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

    @property
    def lookups(self):
        """The last run's library lookups, or None before the first run.

        The lock guards the reference swap only - the contents are immutable
        once built, so readers need nothing further.
        """
        with self._lookups_lock:
            return self._lookups

    def _store_lookups(self, lookups):
        with self._lookups_lock:
            self._lookups = lookups

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

        started = datetime.now()
        error = None
        run_id = None

        # run_id itself is created inside the try so a failure to even open the
        # history DB still hits `finally` below - otherwise the run state is
        # left stuck at "running" forever, since nothing else clears it.
        try:
            run_id = self.storage.start_run()
            self._execute(run_id, started)
            return True
        except Exception as exc:  # noqa: BLE001 - a failed run must not kill the process
            error = str(exc)
            log.error("Run failed: %s", exc)
            log.debug("%s", traceback.format_exc())
            if run_id is not None:
                self.storage.finish_run(
                    run_id, "failed", error=error,
                    duration=(datetime.now() - started).total_seconds(),
                )
            return False
        finally:
            self.state.finish(error=error)
            self.storage.prune()

    def _build_airing(self, media_list, mappings, mal_ids, anidb_ids, owned_ids,
                      local_by_mal, local_by_anidb, sonarr_index,
                      sonarr_available):
        """The currently-airing block, or an unavailable marker on failure.

        `available` is what the page uses to explain itself: an empty list
        because AniList was down reads very differently from an empty list
        because nothing is airing.
        """
        if media_list is None:
            return {
                "entries": [], "available": False, "count": 0,
                "long_runners": 0,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }

        entries = compare.build_season_entries(
            media_list, mappings, mal_ids, anidb_ids,
            sonarr_index, sonarr_available, limit=len(media_list),
            owned_ids=owned_ids,
            local_by_mal=local_by_mal, local_by_anidb=local_by_anidb,
        )

        long_runners = sum(1 for entry in entries if entry.is_long_runner)
        log.info("Airing: %s shows (%s long-runners), %s already in Shoko",
                 len(entries), long_runners,
                 sum(1 for entry in entries if entry.owned))

        return {
            "entries": [entry.to_dict() for entry in entries],
            "available": True,
            "count": len(entries),
            "long_runners": long_runners,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _build_seasons(self, client, settings, mappings, mal_ids, anidb_ids,
                       sonarr_index, sonarr_available, previous_seasons,
                       owned_ids=None, local_by_mal=None, local_by_anidb=None):
        """Ranked charts for the current season and the one either side.

        A failure here keeps the previous run's charts rather than blanking the
        page: the seasonal view is a bonus on top of the run, not its point,
        and an empty table reads as "nothing airing" rather than "fetch failed".
        """
        limit = settings.ui.season_limit
        today = date.today()
        current = (season_mod.season_of(today), today.year)
        upcoming = season_mod.shift(current[0], current[1], 1)
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
                "is_upcoming": (season, year) == upcoming,
                "sorts": {
                    key: [
                        entry.to_dict() for entry in compare.build_season_entries(
                            media, mappings, mal_ids, anidb_ids,
                            sonarr_index, sonarr_available, limit,
                            owned_ids,
                            local_by_mal=local_by_mal,
                            local_by_anidb=local_by_anidb,
                        )
                    ]
                    for key, media in ranked.items()
                },
            })

        log.info("Seasonal charts built for %s",
                 ", ".join(block["label"] for block in blocks))
        return blocks

    # ==========================
    # Autobrr tracking
    # ==========================

    def _refresh_tracked(self, mappings, anilist):
        """Backfill IDs and titles onto rows tracked before they were known.

        A show tracked the week it was announced often has no mapping entry
        and a placeholder title. Both usually land within a run or two, and
        without this the stored row would keep the stale version forever.
        """
        tracked = self.storage.list_autobrr_tracked()
        if not tracked:
            return

        by_id = {int(m["id"]): m for m in anilist if m.get("id")}
        updated = 0

        for row in tracked:
            anilist_id = row["anilist_id"]
            mapping = mappings.get(anilist_id) or {}
            media = by_id.get(anilist_id)

            title = compare.title_of(media) if media else row["title"]
            title_alt = compare.alt_title_of(media, title) if media else row["title_alt"]
            mal_id = str(mapping.get("mal_id") or row["mal_id"] or "")
            anidb_id = str(mapping.get("anidb_id") or row["anidb_id"] or "")

            if (title == row["title"] and title_alt == row["title_alt"]
                    and mal_id == row["mal_id"] and anidb_id == row["anidb_id"]):
                continue

            self.storage.track_autobrr(
                anilist_id, title, title_alt, mal_id, anidb_id, row["source"]
            )
            updated += 1

        if updated:
            log.info("Refreshed %s tracked autobrr show(s) with newer data", updated)

    def _record_statuses(self, statuses):
        """Stamp the AniList status seen for each tracked show.

        Runs before the prune so the grace clock starts on the run a finish is
        first observed, not the run after it.
        """
        if not statuses:
            return

        for row in self.storage.list_autobrr_tracked():
            media = statuses.get(row["anilist_id"])
            if media and media.get("status"):
                self.storage.record_autobrr_status(
                    row["anilist_id"], media["status"]
                )

    def _prune_tracked(self, statuses, mal_ids, anidb_ids,
                       local_by_mal=None, local_by_anidb=None, grace_days=14):
        """Drop tracked shows that are done, cancelled or never coming.

        `statuses` is None when AniList could not be reached, which makes this
        a no-op - see core.autobrr.prune_plan.
        """
        drops = autobrr_mod.prune_plan(
            self.storage.list_autobrr_tracked(), statuses, mal_ids, anidb_ids,
            local_by_mal=local_by_mal, local_by_anidb=local_by_anidb,
            grace_days=grace_days,
        )

        for row, reason in drops:
            # Never exclude: an aged-out show has to stay re-trackable, and an
            # exclusion is indistinguishable from a deliberate "no".
            self.storage.untrack_autobrr(row["anilist_id"], exclude=False)
            log.info("Untracking '%s' from autobrr - %s", row["title"], reason)

        return len(drops)

    def _auto_seed_tracked(self, airing, seasons, limit):
        """Track what's airing and what's coming that Shoko is missing.

        Shows seeded from the upcoming season usually have no MAL or AniDB ID
        yet - they are exactly the ones the mapping file hasn't caught up with
        - so pruning can't recognise them as owned even once Shoko has them.
        _refresh_tracked backfills those IDs on later runs, at which point
        pruning starts working; until then the row can still be untracked by
        hand.
        """
        candidates = autobrr_mod.auto_seed_candidates(
            airing, seasons, self.storage.autobrr_excluded_ids(), limit
        )
        already = self.storage.autobrr_tracked_ids()
        added = 0

        for entry in candidates:
            if entry["anilist_id"] in already:
                continue
            self.storage.track_autobrr(
                entry["anilist_id"], entry["title"], entry.get("title_alt", ""),
                entry.get("mal_id", ""), entry.get("anidb_id", ""),
                autobrr_mod.AUTO,
            )
            log.info("Auto-tracking '%s' for autobrr", entry["title"])
            added += 1

        return added

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

        # ---- airing ----
        # AniList tags media with its *start* season, so a two-cour show is
        # invisible in the season chart it carries over into. This query asks
        # what is airing regardless of season, and is what auto-seeding reads.
        #
        # A failure here must never fail the run: `airing_media` stays None,
        # which makes the prune a no-op rather than untracking everything.
        self.state.set_phase("airing", "Checking what is currently airing")
        airing_media = None
        try:
            airing_media = anilist_client.fetch_airing(
                progress=lambda msg: self.state.set_message(msg)
            )
        except Exception as exc:  # noqa: BLE001 - airing data is best-effort
            log.error("Airing fetch failed - nothing will be untracked this "
                      "run and auto-seeding is skipped: %s", exc)

        # ---- Shoko ----
        self.state.set_phase("shoko", "Reading the Shoko library")
        shoko_client = ShokoClient(settings.shoko, network)
        shoko_series = shoko_client.fetch_series(
            progress=lambda msg: self.state.set_message(msg)
        )

        shoko_entries, mal_ids, anidb_ids, tvdb_ids = compare.build_shoko_index(
            shoko_series, mapping_lookup
        )
        local_by_mal, local_by_anidb = compare.extract_shoko_episode_counts(
            shoko_series
        )
        shoko_episodes, episodes_suspect = compare.count_shoko_episodes(shoko_series)

        self._refresh_tracked(mapping_lookup, anilist)

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
                sonarr_results = compare.compare_sonarr(
                    sonarr_series, tvdb_ids,
                    compare.shoko_episodes_by_tvdb(shoko_entries),
                    mapped_tvdb=compare.mapping_tvdb_ids(mapping_lookup),
                )
            except Exception as exc:  # noqa: BLE001 - Sonarr is optional
                sonarr_error = str(exc)
                log.error("Sonarr fetch failed, continuing without it: %s", exc)
        else:
            log.info("Sonarr not configured - skipping the Sonarr comparison")

        # Configured but unreachable has to read as "unknown" rather than
        # "missing", or a dead Sonarr looks like an empty one.
        sonarr_available = sonarr_enabled and sonarr_error is None

        # Shoko was read before Sonarr, so its rows only learn where they stand
        # now. Cheap second pass rather than reordering the phases, which the
        # progress UI is keyed to.
        compare.annotate_shoko_sonarr(shoko_entries, sonarr_index, sonarr_available)

        # ---- compare ----
        self.state.set_phase("compare", "Matching against your library")
        # AniList relations are AniList IDs; Shoko only knows MAL and AniDB
        # ones. Resolving the library into AniList IDs once here is what lets
        # "the prequel of this is something I own" be answered at all.
        owned_ids = compare.owned_anilist_ids(mapping_lookup, mal_ids, anidb_ids)

        results = compare.compare_collections(
            anilist, mapping_lookup, mal_ids, anidb_ids, settings.anilist,
            sonarr_index, sonarr_available, owned_ids,
            local_by_mal=local_by_mal, local_by_anidb=local_by_anidb,
        )

        airing = self._build_airing(
            airing_media, mapping_lookup, mal_ids, anidb_ids, owned_ids,
            local_by_mal, local_by_anidb, sonarr_index, sonarr_available,
        )

        stats = stats_mod.build_stats(results)
        tiers = stats_mod.build_tiers(results, settings.ui.tiers)
        decades = stats_mod.build_decade_breakdown(results)
        genres = stats_mod.build_genre_breakdown(results)
        comparison = stats_mod.build_comparison(results, sonarr_available)

        previous = self.storage.load_results() or {}
        diff = stats_mod.build_diff(results, previous.get("entries"))

        migration = stats_mod.build_migration_stats(sonarr_results, shoko_entries)
        totals = stats_mod.build_library_totals(
            shoko_series, sonarr_series, shoko_episodes, episodes_suspect
        )

        # ---- seasons ----
        self.state.set_phase("seasons", "Fetching the seasonal charts")
        seasons = self._build_seasons(
            anilist_client, settings, mapping_lookup, mal_ids, anidb_ids,
            sonarr_index, sonarr_available, previous.get("seasons"), owned_ids,
            local_by_mal, local_by_anidb,
        )

        # ---- autobrr ----
        # Pruning lives here rather than back in the Shoko phase because the
        # decision now turns on AniList status, not on ownership alone.
        self.state.set_phase("autobrr", "Updating the autobrr list")

        statuses = None
        if airing_media is not None:
            statuses = {
                int(media["id"]): {
                    "status": media.get("status"),
                    "episodes_aired": compare.aired_episodes(media),
                }
                for media in list(anilist) + list(airing_media)
                if media.get("id")
            }

        self._record_statuses(statuses)
        self._prune_tracked(
            statuses, mal_ids, anidb_ids, local_by_mal, local_by_anidb,
            settings.autobrr.finished_grace_days,
        )
        # The upcoming season block seeds regardless of whether the airing
        # fetch worked - it's independent data, and skipping it would lose the
        # head start on next season's sequels for no reason.
        self._auto_seed_tracked(
            airing["entries"] if airing_media is not None else None,
            seasons,
            settings.autobrr.auto_seed_limit,
        )
        autobrr_tracked = self.storage.list_autobrr_tracked()

        if settings.autobrr.configured:
            try:
                AutobrrClient(settings.autobrr, network).trigger_list_refresh()
            except Exception as exc:  # noqa: BLE001 - autobrr is optional
                log.error("Autobrr refresh failed, continuing: %s", exc)

        # ---- persist ----
        self.state.set_phase("persist", "Saving results")
        duration = (datetime.now() - started).total_seconds()

        # Held for the season picker, which has to match an on-demand fetch
        # against Shoko and Sonarr outside of any run.
        self._store_lookups(LookupContext(
            mappings=mapping_lookup,
            mal_ids=mal_ids,
            anidb_ids=anidb_ids,
            local_by_mal=local_by_mal,
            local_by_anidb=local_by_anidb,
            sonarr_index=sonarr_index,
            sonarr_available=sonarr_available,
            built_at=datetime.now(),
        ))

        today = date.today()
        current_season = season_mod.season_of(today)
        current_year = today.year

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(duration, 1),
            "entries": [entry.to_dict() for entry in results],
            "sonarr": [entry.to_dict() for entry in sonarr_results],
            # Only the rows the page actually lists. Persisting the whole Shoko
            # library would add hundreds of KB to every /api/results fetch to
            # say "in both", which the Sonarr side already covers.
            "shoko": [entry.to_dict() for entry in compare.shoko_only(shoko_entries)],
            "stats": stats,
            "tiers": tiers,
            "decades": decades,
            "genres": genres,
            "diff": diff.to_dict(),
            "migration": migration,
            "totals": totals,
            "comparison": comparison,
            "seasons": seasons,
            "airing": airing,
            "current_season": {"season": current_season, "year": current_year},
            "autobrr_tracked": len(autobrr_tracked),
            "autobrr_enabled": bool(settings.autobrr.configured),
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
