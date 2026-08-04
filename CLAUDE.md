# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A self-hosted Flask web app that compares an AniList ranked list against a
[Shoko](https://shokoanime.com/) library, tracks a Sonarr → Shoko migration,
and hands off currently-airing gaps to autobrr. Runs as a Docker container
with an in-process scheduler (`croniter`, no cron daemon) and a built-in
dashboard. Configuration is entirely through the web UI — nothing is meant to
be hand-edited beyond initial deployment.

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
mappings → fetch AniList list → fetch Shoko library → extract MAL/AniDB/TVDB
IDs → refresh/prune autobrr-tracked rows → fetch Sonarr (optional) → compare
everything (`core/compare.py`) → build stats/tiers/decades/genres/diff
(`core/stats.py`) → build seasonal charts (`core/seasons.py`) → auto-seed
autobrr tracking → persist a single JSON payload via `storage.py` and update
run history. `RunState` prevents two runs happening concurrently
(`try_begin`/`finish`).

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
unauthenticated by design so autobrr can poll it) of currently-airing shows
Shoko is missing. Auto-tracked entries are re-pruned once Shoko has them;
manually-untracked entries are remembered so auto-seed doesn't re-add them
(`core/autobrr.py`, `storage.py` tracked-rows table).

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
