# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A self-hosted Flask web app that compares an AniList ranked list against a
[Shoko](https://shokoanime.com/) library, tracks a Sonarr → Shoko migration,
and hands off currently-airing gaps to autobrr. Runs as a Docker container
with an in-process scheduler (`croniter`, no cron daemon) and a built-in
dashboard. Configuration is entirely through the web UI — nothing is meant to
be hand-edited beyond initial deployment.

## Starting work

**Always branch from a freshly fetched `origin/main`, never from whatever is
checked out.** Feature branches here are merged via PR and then left behind, so
the checked-out branch is usually a dead one several days stale.

```bash
git fetch origin && git checkout -b <name> origin/main
```

`git status`, `git branch` and `git log` are all local-only and report a stale
clone as perfectly healthy — a clean tree says nothing about whether `main` has
moved. This has already cost one full rebuild of a feature: work branched off a
merged branch duplicated `sequel_of_owned`, `matches_shoko` and the
`_shoko_series_ids` extraction that this file already warned against forking,
because this file was itself in the unfetched commits.

## Commands

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/pip install pytest ruff

./venv/bin/pytest -q                    # run the full suite
./venv/bin/pytest -q tests/test_compare.py   # single file
./venv/bin/pytest -q tests/test_compare.py::test_name  # single test
./venv/bin/ruff check app tests         # lint (also runs in CI)

CONFIG_DIR=./dev-config ./venv/bin/python app/main.py   # run locally
```

CI (`.github/workflows/docker-build.yml`) runs `ruff check app tests` and
`pytest -q` on every push/PR, then builds and pushes a multi-tag image to
GHCR (`ghcr.io/dubberness/anime-tracker`) on pushes to `main` and version
tags. PRs only run the test job.

**Python version**: code is kept **3.9-compatible** so the test suite runs
against a system Python without building the container; the image itself
runs 3.12. Don't use 3.10+-only syntax (e.g. `match`, `X | Y` unions in
runtime code).

## Architecture

Everything is wired together once in `app/main.py::build_context()` into an
`AppContext` (runtime, config, storage, state, runner, scheduler) that both
the Flask layer and the scheduler share. `main.py` configures logging before
any other app module is imported (`logging_setup.configure()` must run
first), which is why it and `tests/conftest.py` have `noqa: E402` on their
late imports — don't "fix" that ordering.

```
app/
  main.py          AppContext wiring, signal handling, one-shot (--once) mode
  config.py        dataclass schema, JSON persistence, v1->v2 migration, env overrides
  storage.py       SQLite run history + latest-results cache
  runner.py        Runner: the actual tracking run (fetch -> compare -> persist)
  scheduler.py      croniter-based in-process scheduler
  state.py         thread-safe RunState (current phase/message, in-progress guard)
  logging_setup.py stdout logging + in-memory ring buffer (powers the Logs page)
  clients/         AniList, Shoko, Sonarr, Autobrr HTTP clients + mapping loader
  core/            pure logic: compare, stats, seasons, autobrr — no I/O, easy to test
  web/              Flask app: server.py (pages + JSON API), templates/, static/
tests/             pytest suite, mirrors app/ modules
```

**Data flow of a run** (`Runner._execute` in `app/runner.py`): load ID
mappings → fetch AniList list → fetch what's airing (`status: RELEASING`) →
fetch Shoko library → `build_shoko_index` (per-series rows *and* the
MAL/AniDB/TVDB sets, one walk) + `extract_shoko_episode_counts` → refresh
autobrr-tracked rows → fetch Sonarr (optional) → `annotate_shoko_sonarr` fills
the Shoko rows' Sonarr status → compare everything (`core/compare.py`) → build
stats/tiers/decades/genres/diff (`core/stats.py`) → build seasonal charts
(`core/seasons.py`) → record statuses, prune and auto-seed autobrr tracking →
persist a single JSON payload via `storage.py` and update run history.
`RunState` prevents two runs happening concurrently (`try_begin`/`finish`).

Pruning runs in the **autobrr** phase, not the Shoko one, because the decision
turns on AniList status rather than ownership alone. `_record_statuses` runs
immediately before it so the grace clock starts on the run a finish is first
seen, not the one after.

Shoko is read *before* Sonarr and the phase order is load-bearing (`state.PHASES`
and the progress UI follow it), which is why the Shoko rows learn their Sonarr
status in a second pass rather than being built with it. Don't reorder the
phases to avoid that pass.

`extract_shoko_ids` and `count_shoko_episodes` are thin wrappers over
`build_shoko_index` / `shoko_episode_count`. The per-version ID rules live in
`_shoko_series_ids` alone — that logic is subtle (see its docstring) and must
not be forked. The wrappers exist for the diagnostics endpoint and to keep the
existing tests as a regression guard.

Only the Shoko rows the migration page lists (`compare.shoko_only`) go into the
payload. Persisting the whole library would add hundreds of KB to every
`/api/results` fetch to say "in both", which the Sonarr side already covers.

**Config precedence** (low to high): dataclass defaults → `config.json` →
environment variables. Env-overridden settings are reported as locked so the
settings UI greys them out rather than silently failing to persist a change
(see `app/config.py` module docstring and `ENV_OVERRIDES`). A missing/
half-finished config is a normal "needs setup" state, not a startup failure
— the app boots and nags via the settings page instead of exiting.

**Matching logic** (all in `core/compare.py`, pure/testable):
- AniList ↔ Shoko: MAL ID *or* AniDB ID (either is sufficient), via the
  Kometa Anime-IDs mapping file. The AniDB ID is the *key* of each mapping
  object, not a field on it.
- AniList ↔ Sonarr / Sonarr ↔ Shoko: TVDB ID. Where the mapping names a TVDB
  season, per-season file counts are used so season 1 on disk doesn't imply
  season 2 is owned.
- Shoko exposes IDs inconsistently across versions — both `IDs.*` and the
  `Links` list are checked (`Settings → Diagnostics → Check Shoko ID fields`
  surfaces what a given instance actually returns).
- One Shoko series can hold **two** TVDB IDs (its own, possibly stale, plus the
  mapping-derived one). Match against all of them and record whichever hit —
  picking the first would report migrated series as Shoko-only.
- A Shoko series with no TVDB ID is `unmapped`, not `missing`. Most of a library
  is movies and OVAs; collapsing that distinction into a bool floods the
  "only in Shoko" view with false positives.
- The same on the Sonarr side: `SonarrEntry.unmappable` marks a series whose
  TVDB ID nothing can reach (`compare.mapping_tvdb_ids`), so no answer is
  possible either way and the page keeps it out of the work list. That set is
  derived from `_anidb_to_tvdb` — the very index the matching crosses — so
  "reachable" means the same thing in both places rather than two similar walks
  that could drift. Typical cause is TheTVDB splitting a series the mapping
  still records under the old combined entry.
- `matches_shoko` is the one home of the MAL-or-AniDB ownership rule. `Entry.owned`,
  `owned_anilist_ids` and `autobrr.is_now_owned` all go through it; the flags it
  feeds are rendered side by side, so a second copy would show up as the same
  row disagreeing with itself.
- `sequel_of_owned` reads `PREQUEL` edges (AniList IDs) and resolves them
  through the AniList-ID-keyed mapping into the Shoko sets, via
  `owned_anilist_ids`. `_relation_nodes` returns `(id, format)` from one walk
  because the same edges are read two ways: `sequel_of_owned` keeps only
  `SEASON_FORMATS` (a node with no `format` is kept — a cached AniList response
  predates that field being requested), while `is_franchise_root` deliberately
  stays **unfiltered**.

**Graceful degradation is a deliberate, repeated pattern** — preserve it
when touching these paths:
- AniList unreachable → serve the stale cache instead of failing the run.
- Sonarr fetch fails → rest of the run completes, Sonarr column reads
  "unknown" (`sonarr_available = sonarr_enabled and sonarr_error is None`),
  never rendered as an empty library.
- Seasonal fetch fails → previous run's seasonal charts are kept
  (`_build_seasons` returns `previous_seasons`) rather than blanking the page.
- Autobrr push fails → the list endpoint still serves from the local DB;
  only the instant-refresh notification is lost.
- A failed run must not kill the process (`_run_guarded` catches broadly and
  logs) — the whole point of the guard is process survival.

**Autobrr integration**: the app doesn't configure autobrr's indexers or
filters — it only maintains a plaintext title list (`GET /api/autobrr/list`,
unauthenticated by design so autobrr can poll it). Manually-untracked entries
are remembered so auto-seed doesn't re-add them (`core/autobrr.py`,
`storage.py` tracked-rows table).

**Airing is a status, not a season.** AniList tags media with the season it
*started* in, so a two-cour show drops off the current chart halfway through
its run while still going out weekly. `clients/anilist.py::fetch_airing`
queries `status: RELEASING` instead, and that list — not the current season's
chart — is what seeds tracking. Don't reintroduce the current-season block as a
seed source; it structurally cannot see a carryover, which is how an airing
show went untracked in the first place.

**Ownership is not doneness, and neither is being caught up.** Three distinct
states — collapsing any two of them has already caused a bug:

| state | means | check |
|---|---|---|
| `owned` | Shoko has *at least one* episode | `matches_shoko` |
| complete | Shoko has every episode that has **aired so far** | `compare.is_complete` |
| done | complete **and** nothing more is coming | `autobrr.is_done` |

Shoko registers a series on its first episode, so `owned` means "started".
`is_complete` falls back to plain `owned` when the aired count is unknown, so
pre-4.3 payloads render unchanged. Owning an earlier cour must never block
tracking: a split-cour part two is a separate AniList entry mapped to part
one's MAL ID.

**Only `is_done` may gate seeding or hide the Track button.** A weekly show is
"complete" every week between the latest episode landing and the next one
airing — gating on `is_complete` silently dropped caught-up shows off the list
and left no way to re-add them by hand. `STILL_COMING` (`RELEASING`,
`NOT_YET_RELEASED`, `HIATUS`) is what keeps them trackable; an unrecognised or
missing status is deliberately excluded so older payloads behave as before.
`app.js` mirrors all three (`isComplete` / `isDone` / `STILL_COMING`) — change
both sides together.

`should_stay_tracked` in `core/autobrr.py` is the whole lifecycle in one pure
function, returning `(keep, reason)` so no untrack is mysterious in the log.
Two invariants there are load-bearing: **a `None` status always keeps** (an
AniList outage handing autobrr an empty list would silently stop every grab),
and **every drop uses `exclude=False`** (an aged-out show must stay
re-trackable). The grace window exists because AniList flips to `FINISHED` when
the finale *airs*, hours before a release group posts it.

`auto_seed_candidates(airing, seasons, ...)` takes the airing entries **and**
the whole seasons list; `upcoming_blocks` picks only the upcoming one, since
the airing list already covers the current season and more. Both callers
(`Runner._auto_seed_tracked` and `server._auto_seed_ids`) must go through it:
`_auto_seed_ids` is what decides whether untracking also records an exclusion,
so if the two ever disagreed about which shows are auto-picks, the next run
would silently re-add something the user removed. `auto_seed_limit` is the
top-N **per source**, not shared: each contributes its top `limit`, then any
`sequel_of_owned` entry ranked *below* that cutoff is added as well. A sequel
inside the cutoff is just one of the N and consumes a slot — there is no
reordering. A limit of 0 means "track nothing at all", sequels included.
Long-runners (`compare.is_long_runner`) are never auto-seeded.

The season picker (`GET /api/season/<year>/<season>`) needs the last run's
Shoko/Sonarr lookups at request time, so `Runner` holds a `LookupContext`
between runs behind a lock. Don't move that into `results.json` — the browser
downloads that whole payload on every Library load.

## Conventions

- `core/` modules are pure logic with no network/DB access — keep it that
  way so they stay unit-testable without mocking I/O.
- `clients/` modules own all outbound HTTP; failures there are caught by
  callers (`runner.py`), not inside the client, except where a client is
  inherently optional (Sonarr, Autobrr).
- Broad `except Exception` blocks in `runner.py`/`main.py` are intentional
  (annotated `# noqa: BLE001`) and paired with a comment explaining why that
  specific failure must not propagate — match that style rather than
  narrowing them reflexively.
- Secrets (API keys) are masked on `GET /api/settings` and never sent to the
  browser in full; preserve that when touching settings serialization.
- ruff config lives in `pyproject.toml`: `select = ["E", "W", "F", "I", "B",
  "C4"]`, line length 100, first-party import groups listed under
  `[tool.ruff.lint.isort]` — add new top-level `app/` modules there if you
  create one.
