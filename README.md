# Anime Collection Tracker

A self-hosted web app that compares an AniList ranked list against your
[Shoko](https://shokoanime.com/) library, and tracks a Sonarr → Shoko
migration. Runs on a schedule in Docker, with a built-in dashboard and
settings UI.

![version](https://img.shields.io/badge/version-4.2-blue)

## What it does

- **Collection tracking** — how much of the AniList top *N* you actually own,
  broken down by rank tier, decade and genre.
- **Two libraries side by side** — every tracked show carries both its Shoko
  and its Sonarr status, so "in Shoko only", "in Sonarr only" and "in neither"
  are one click apart.
- **Seasonal charts** — the top shows of this season and the one either side,
  with the same Shoko/Sonarr status on each.
- **Autobrr hand-off** — the currently-airing shows Shoko is missing, published
  as a title list autobrr can grab from.
- **Recommendations** — the highest-value things you're missing, filtered to
  franchise roots so sequels don't clutter the list.
- **Migration tracking** — which Sonarr series are already in Shoko, matched on
  TVDB ID, and how much is left to move.
- **Trend history** — completion over time, so progress is visible run to run.

Everything is configured from the web UI. Nothing needs to be edited by hand.

## Quick start

```bash
docker run -d \
  --name anime-tracker \
  --restart unless-stopped \
  -p 8080:8080 \
  -v /mnt/user/appdata/anime-tracker:/config \
  -e PUID=99 -e PGID=100 -e TZ=Australia/Hobart \
  ghcr.io/dubberness/anime-tracker:latest
```

Then open `http://<host>:8080/`. It lands on the settings page — add your Shoko
URL and API key, hit **Test connection**, then **Save**. The first run starts
automatically.

The anime ID mapping file downloads itself; there is no manual setup step.

### Unraid

Use **Docker → Add Container** so Unraid stores a template (a container created
by raw `docker run` shows without an icon or WebUI button). Or import
[`unraid-template.xml`](unraid-template.xml).

| Field | Value |
|---|---|
| Repository | `ghcr.io/dubberness/anime-tracker:latest` |
| WebUI | `http://[IP]:[PORT:8080]/` |
| Port | `8080` → `8080` |
| Path | `/mnt/user/appdata/anime-tracker` → `/config` |
| Variables | `PUID=99`, `PGID=100`, `TZ=Australia/Hobart` |

### Compose

```bash
docker compose up -d
```

## Configuration

All settings live in `/config/config.json` and are editable from the
**Settings** page:

| Setting | Default | Notes |
|---|---|---|
| Shoko URL / API key | — | Required |
| Sonarr URL / API key | — | Optional; migration section hides without it |
| Autobrr URL / API key / list ID | — | Optional; only for pushing an instant list refresh |
| Auto-track the season's top | `10` | How many airing shows a run tracks by itself |
| Formats | `TV` | TV, Movie, OVA, ONA, Special… |
| Ranked by | Popularity | Popularity, Score, Trending, Favourites |
| How many | `1000` | How far down the list to track |
| Minimum popularity | `50000` | Skips obscure entries |
| Tiers | `100, 250, 500, 1000` | Rank tiers for the progress bars |
| Shows per season | `20` | How many per season on the Seasons page (max 50) |
| Cron schedule | `0 4 * * *` | Container timezone |
| Run on start | on | Run immediately when the container starts |
| Cache lifetime | `24h` | How long before AniList is re-fetched |

### Environment variables

Deployment-level only. **Any setting also set as an env var overrides the
config file and is greyed out in the UI** — so leave these unset unless you
specifically want to pin a value.

| Variable | Default | Purpose |
|---|---|---|
| `PUID` / `PGID` | `99` / `100` | Unraid `nobody:users` |
| `TZ` | `Australia/Hobart` | Schedule and timestamps |
| `WEB_PORT` | `8080` | Dashboard port |
| `SERVE_WEB` | `true` | Set `false` for a headless scheduler |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose logs |
| `RUN_ONCE` | unset | Run once and exit (no server/scheduler) |
| `CONFIG_DIR` | `/config` | Where state is stored |
| `SHOKO_URL`, `SHOKO_API_KEY`, `SONARR_URL`, `SONARR_API_KEY`, `AUTOBRR_URL`, `AUTOBRR_API_KEY`, `AUTOBRR_LIST_ID`, `CRON_SCHEDULE`, `RUN_ON_START`, `MAX_RESULTS`, `MIN_POPULARITY`, `CACHE_MAX_AGE_HOURS`, `MAPPING_FILE` | unset | Pin a setting and lock it in the UI |

## Layout

```
Dockerfile
docker-compose.yml
unraid-template.xml       Unraid Docker template
entrypoint.sh             PUID/PGID + timezone, then drops privileges
pyproject.toml            ruff + pytest config
app/
  main.py                 wiring, signals, startup
  config.py               schema, persistence, v1 migration, env overrides
  storage.py              SQLite run history + latest results
  runner.py               the tracking run
  scheduler.py            cron loop
  state.py                thread-safe run state
  logging_setup.py        stdout logging + in-memory ring buffer
  clients/                anilist, shoko, sonarr, autobrr, mappings
  core/                   compare, stats, seasons, autobrr, models (pure logic)
  web/                    Flask app, templates, static assets
tests/                    pytest suite
```

## How matching works

- **AniList ↔ Shoko** — matched on **MAL ID or AniDB ID** (either is enough),
  via the [Kometa Anime-IDs](https://github.com/Kometa-Team/Anime-IDs) mapping.
  The AniDB ID is the *key* of each mapping object rather than a field on it,
  which is easy to miss.
- **AniList ↔ Sonarr** — matched on the **TVDB ID** from the same mapping.
  Roughly half the mapped entries have one; the rest show *No TVDB ID* rather
  than a misleading "not in Sonarr". Where the mapping names a TVDB season, the
  per-season file count is used, so season 1 being on disk doesn't mark season
  2 as owned.
- **Sonarr ↔ Shoko** — matched on **TVDB ID**.
- Shoko exposes IDs differently across versions, so both `IDs.*` and the
  `Links` list are checked. If match rates look wrong, use
  **Settings → Diagnostics → Check Shoko ID fields** to see what your instance
  actually returns.
- **Franchise root** = an entry with no `PREQUEL` relation. One-hop check, so a
  franchise with gaps could slip through.
- **Recommendation score** = `averageScore × 0.8 + log10(popularity) × 10`.

### Sonarr status

| Shown as | Means |
|---|---|
| ✓ In Sonarr | Sonarr has it, with episode files on disk |
| Monitored | Sonarr has the series, but nothing downloaded yet |
| Not in Sonarr | The TVDB ID is known and Sonarr doesn't have it |
| No TVDB ID | The mapping file has no TVDB ID — Sonarr can't be checked |
| — | Sonarr is switched off, or the last run couldn't reach it |

## Seasons

The **Seasons** page lists the top shows of the current season plus the one
either side, each ranked by popularity, trending or score — the toggle switches
between rankings without a refetch, since a run collects all three.

Ranking comes from **AniList**, not AniDB: AniDB's HTTP API has no endpoint that
returns a season's shows or any seasonal ranking (only `anime` by AID,
`hotanime`, `main`, the random ones and the titles dump), and it requires a
registered client with strict rate limits.

Shows are matched against Shoko exactly as the library page does. Ones airing
next season are often too new for the mapping file, so they legitimately show as
unmatched rather than missing.

## Autobrr

The Seasons page can hand currently-airing shows to
[autobrr](https://autobrr.com/), which watches your indexers and grabs new
episodes as they're released — the piece that replaces Sonarr once you've
moved off it.

**This does not configure autobrr.** Indexers, quality, release groups and the
download client stay in the filter you already have there. All this does is
tell that filter *which shows to match*, using autobrr's built-in
[Lists](https://autobrr.com/filters/lists) feature.

### One-time setup

1. On the **Settings** page, copy the **List URL**.
2. In autobrr: **Settings → Lists → New**, choose the plaintext/custom type,
   paste the URL, and select your anime filter as the target.
3. Optionally fill in autobrr's URL, API key and the new list's ID back on the
   settings page. That's only needed so a run can tell autobrr to re-read the
   list immediately — without it, autobrr picks changes up on its own every
   six hours.

### What ends up on the list

| | |
|---|---|
| **Automatic** | Each run tracks the current season's top *N* by popularity (default 10) that Shoko doesn't have |
| **Manual** | **Track** on any row of the Seasons page — including previous/next season, or past the automatic cutoff |
| **Removed** | Automatically, once Shoko has the show — there's nothing left to grab |
| **Untracked by hand** | Stays off. Untracking one of the automatic picks records the choice so the next run doesn't re-add it; the freed slot goes to the next-ranked show |

Each show contributes its English **and** romaji title where they differ, since
release groups use one or the other.

The list endpoint is unauthenticated so autobrr can poll it, and returns an
empty body when nothing is tracked — that's a normal state, not an error. Like
the rest of the app it assumes a trusted LAN.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness (used by the Docker healthcheck) |
| `GET /api/status` | Current run state |
| `GET /api/results` | Full latest result set |
| `GET /api/history` | Run history for the trend chart |
| `POST /api/run` | Trigger a run |
| `GET/POST /api/settings` | Read/update settings (secrets masked on read) |
| `POST /api/test/<service>` | Test a connection |
| `GET /api/diagnostics/shoko` | Report Shoko's ID field shapes |
| `GET /api/autobrr/list` | The plaintext title list autobrr polls |
| `GET /api/autobrr/tracked` | Tracked shows as JSON |
| `POST /api/autobrr/track` | Track a show |
| `DELETE /api/autobrr/track/<id>` | Untrack a show |

## Development

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/pip install pytest ruff

./venv/bin/pytest -q
./venv/bin/ruff check app tests

CONFIG_DIR=./dev-config ./venv/bin/python app/main.py
```

Code is kept Python 3.9-compatible so the suite runs against a system Python
without building the image; the container itself runs 3.12.

## Notes

- Scheduling is in-process (`croniter`) — no cron daemon in the image.
- If AniList is unreachable, a stale cache is used rather than failing the run.
- Sonarr failures degrade gracefully; the rest of the run still completes, and
  the Sonarr column reads *unknown* rather than pretending the library is empty.
- A failed seasonal fetch keeps the previous run's charts instead of blanking
  the page.
- Autobrr failures degrade gracefully too — the list is always served from the
  local database, so only the instant-refresh push is lost.
- Secrets are masked in the settings UI and never sent to the browser in full.
- CSV export was removed in v4 — the dashboard and `/api/results` replace it.
- Fixed in 4.1: AniDB IDs were never loaded from the mapping file, so matching
  had been silently falling back to MAL alone. Expect the Shoko match rate to
  go up on the first run after upgrading.
