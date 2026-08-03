# Anime Collection Tracker

A self-hosted web app that compares an AniList ranked list against your
[Shoko](https://shokoanime.com/) library, and tracks a Sonarr → Shoko
migration. Runs on a schedule in Docker, with a built-in dashboard and
settings UI.

![version](https://img.shields.io/badge/version-4.0-blue)

## What it does

- **Collection tracking** — how much of the AniList top *N* you actually own,
  broken down by rank tier, decade and genre.
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
| Formats | `TV` | TV, Movie, OVA, ONA, Special… |
| Ranked by | Popularity | Popularity, Score, Trending, Favourites |
| How many | `1000` | How far down the list to track |
| Minimum popularity | `50000` | Skips obscure entries |
| Tiers | `100, 250, 500, 1000` | Rank tiers for the progress bars |
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
| `SHOKO_URL`, `SHOKO_API_KEY`, `SONARR_URL`, `SONARR_API_KEY`, `CRON_SCHEDULE`, `RUN_ON_START`, `MAX_RESULTS`, `MIN_POPULARITY`, `CACHE_MAX_AGE_HOURS`, `MAPPING_FILE` | unset | Pin a setting and lock it in the UI |

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
  clients/                anilist, shoko, sonarr, mappings
  core/                   compare, stats, models (pure logic)
  web/                    Flask app, templates, static assets
tests/                    pytest suite
```

## How matching works

- **AniList ↔ Shoko** — matched on **MAL ID or AniDB ID** (either is enough),
  via the [Kometa Anime-IDs](https://github.com/Kometa-Team/Anime-IDs) mapping.
- **Sonarr ↔ Shoko** — matched on **TVDB ID**.
- Shoko exposes IDs differently across versions, so both `IDs.*` and the
  `Links` list are checked. If match rates look wrong, use
  **Settings → Diagnostics → Check Shoko ID fields** to see what your instance
  actually returns.
- **Franchise root** = an entry with no `PREQUEL` relation. One-hop check, so a
  franchise with gaps could slip through.
- **Recommendation score** = `averageScore × 0.8 + log10(popularity) × 10`.

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
- Sonarr failures degrade gracefully; the rest of the run still completes.
- Secrets are masked in the settings UI and never sent to the browser in full.
- CSV export was removed in v4 — the dashboard and `/api/results` replace it.
