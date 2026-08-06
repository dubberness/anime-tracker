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
  with the same Shoko/Sonarr status on each, and a **New season** badge on
  anything whose earlier season is already in Shoko.
- **Autobrr hand-off** — the currently-airing shows Shoko is missing, published
  as a title list autobrr can grab from, with new seasons of what you already
  have taking priority.
- **Recommendations** — the highest-value things you're missing, filtered to
  franchise roots so sequels don't clutter the list.
- **Migration tracking** — the whole Sonarr ↔ Shoko picture: what's left to
  move, what's in both, what only Shoko has, and what moved but came up short.
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
| Auto-track each season's top | `10` | Counted separately for this season and the next; new seasons of what you own are tracked even below the cutoff |
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
- **Sonarr ↔ Shoko** — matched on **TVDB ID**. Shoko's own TVDB field is
  cross-checked against the mapping file (keyed by each series' AniDB ID),
  since Shoko dropped TheTVDB as a metadata source and that field can be
  stale or simply wrong for a given title.
- Shoko exposes IDs differently across versions, so both `IDs.*` and the
  `Links` list are checked. If match rates look wrong, use
  **Settings → Diagnostics → Check Shoko ID fields** to see what your instance
  actually returns.
- **Franchise root** = an entry with no `PREQUEL` relation. One-hop check, so a
  franchise with gaps could slip through.
- **New season of something you own** = an entry whose `PREQUEL` resolves,
  through the mapping file, to a series Shoko already has. Also one hop, so
  owning season 1 but not season 2 won't flag season 3. Prequels that are
  specials or recaps are ignored, since those aren't the season before.
- **Recommendation score** = `averageScore × 0.8 + log10(popularity) × 10`.

### Sonarr status

| Shown as | Means |
|---|---|
| ✓ In Sonarr | Sonarr has it, with episode files on disk |
| Monitored | Sonarr has the series, but nothing downloaded yet |
| Not in Sonarr | The TVDB ID is known and Sonarr doesn't have it |
| No TVDB ID | The mapping file has no TVDB ID — Sonarr can't be checked |
| — | Sonarr is switched off, or the last run couldn't reach it |

## Migration

The **Migration** page shows both sides of the Sonarr → Shoko move, because
Sonarr's view alone can't say where a series came from.

| Section | What it means |
|---|---|
| **Still only in Sonarr** | The work list — Sonarr has files, Shoko doesn't |
| **Can't be checked** | Sonarr's TVDB ID appears nowhere in the mapping file, so there's no route to the AniDB IDs Shoko reports. Unanswerable, not a no — kept out of the work list |
| **Moved, but short** | In both, but Shoko holds fewer episodes than Sonarr — usually a half-finished move |
| **Only in Shoko** | Shoko has the only copies. *Not in Sonarr* = Sonarr never had the series; *Monitored* = Sonarr still tracks it with nothing on disk, so the entry is a leftover |
| **Already in both** | Matched on TVDB ID. Goes on the series existing in Sonarr, not on what it holds, so *Monitored* rows appear here too |

Two caveats worth knowing:

- **Episode counts aren't strictly comparable.** Shoko's local count generally
  leaves specials out where Sonarr's includes season 0 when monitored, so a gap
  of one or two usually means nothing. Both raw numbers are shown rather than a
  verdict, and a one-episode gap is treated as even.
- **Series with no TVDB ID can't be placed.** Most movies and OVAs, plus
  anything the mapping file hasn't caught up with, have no TVDB ID at all —
  they're counted separately rather than listed as Shoko-only, which would
  otherwise bury the rows that mean something.
- **The same applies in reverse, and it's easy to miss.** When TheTVDB splits
  a series the mapping still records under the old combined entry, Sonarr ends
  up on the new ID and nothing can bridge it. *Digimon Adventure 02* is the
  worked example: Sonarr has it under TVDB 459436, the mapping maps AniDB 561
  to TVDB 72241 season 2 (the old bundled *Digimon: Digital Monsters* entry),
  and no mapping row mentions 459436 at all. Shoko has the show; the app simply
  can't prove it. Those land under **Can't be checked** rather than in the work
  list. The fix is a mapping row upstream in
  [Kometa Anime-IDs](https://github.com/Kometa-Team/Anime-IDs).

Note that "can't be checked" is judged against the same mapping lookup the
matching itself uses — which only keeps rows carrying an AniList ID, since that
is what the rest of the app is keyed on. A TVDB ID present in the raw file but
on a row without an AniList ID is still unreachable in practice, and is counted
here as such.

A run where Sonarr couldn't be reached records zeros in the history table, and
the progress chart skips those rather than drawing a drop to 0%.

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
| **Automatic** | Each run tracks the top *N* by popularity (default 10) that Shoko doesn't have **from each of** the current season and the next — so the default is up to 20 — plus any new season of something you already own that ranked below the cutoff |
| **Manual** | **Track** on any row of the Seasons page — including the previous season, or past the automatic cutoff |
| **Removed** | Automatically, once Shoko has the show — there's nothing left to grab |
| **Untracked by hand** | Stays off. Untracking one of the automatic picks records the choice so the next run doesn't re-add it; the freed slot goes to the next-ranked show |

Seeding from the upcoming season means a sequel is on the list before it starts
airing rather than after the first episode has gone by. Two consequences worth
knowing: a not-yet-released title sits on the list for weeks, so autobrr will
match anything bearing that name (a pre-air leak, a promo, an unrelated film in
the same franchise) — that's your filter's quality rules to handle, since this
app deliberately doesn't own them. And next season's shows are exactly the ones
the mapping file hasn't caught up with, so they have no MAL or AniDB ID yet and
automatic removal can't recognise them as owned until it does; a later run
backfills the IDs and removal starts working. Untracking by hand works
throughout.

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
- Fixed after 4.2.0: Sonarr ↔ Shoko migration matching now also cross-checks
  the mapping file for each series' TVDB ID, not just Shoko's own (sometimes
  stale) field. Expect a few migration entries to flip to "already in Shoko"
  on the first run after upgrading.
- Fixed after 4.2.0: a configured-but-unreachable Sonarr made the Migration
  page render a confident "0 of 0 — 0%". It now says so and holds the figures
  instead, matching how the library page already behaved.
- New after 4.2.0: Sonarr series whose TVDB ID the mapping file has never heard
  of are now shown as **Can't be checked** instead of sitting in the work list
  as though Shoko were missing them. Expect "still only in Sonarr" to drop by
  however many of those you have — nothing changed about what's on disk, and
  the stored completion percentage is unaffected.
- New after 4.2.0: the Migration page gained the Shoko side of the comparison
  ("only in Shoko", and moves that came up short on episodes). The headline
  percentage and the run history are unchanged — the new sections are additive,
  so nothing you were tracking moves.
- New after 4.2.0: autobrr seeding now also draws on the upcoming season, and
  the auto-track count applies **per season** rather than in total — so the
  default of 10 now tracks up to 20 by popularity where it previously tracked
  10, plus any new season of something you own that ranked below the cutoff.
  Expect the list to grow on the first run after upgrading; drop
  the count on the settings page if that's more than you want, and untracking
  any individual pick keeps it off.
- The `season limit` default rose from 20 to 30, but **an existing install
  keeps whatever is already in its `config.json`** — defaults only apply to
  settings that aren't present. Change it on the settings page if you want the
  wider charts.
