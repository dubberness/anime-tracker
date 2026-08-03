# Anime Collection Tracker

Compares an AniList Top TV list against your Shoko library, and tracks your
Sonarr → Shoko migration. Outputs CSVs and an HTML dashboard, served over HTTP,
on a schedule.

Rewritten from PowerShell to Python — no PowerShell runtime, image drops from
roughly 1.2 GB to around 150 MB.

## Layout

```
Dockerfile
docker-compose.yml
entrypoint.sh          PUID/PGID + timezone, then drops privileges
requirements.txt
config.example.json    reference only - real config.json is gitignored
.github/workflows/
  docker-build.yml     builds + pushes to GHCR on push to main
app/
  main.py              orchestration, scheduler, web server
  config.py            config loading and validation
  clients.py           AniList / Shoko / Sonarr APIs, retry+backoff
  compare.py           ID matching, franchise roots, stats, diffs
  report.py            CSV + HTML dashboard
```

## Getting this onto GitHub

```bash
cd anime-tracker-native
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/anime-tracker.git
git push -u origin main
```

Create the repo on GitHub first (github.com → New repository), or via the CLI:
`gh repo create anime-tracker --private --source=. --push`.

The included `.github/workflows/docker-build.yml` builds and pushes the image
to GitHub Container Registry (GHCR) automatically on every push to `main` —
no extra setup needed, it uses the token GitHub provides to Actions by
default. After your first push, check the **Actions** tab to watch it build,
then **Packages** (on your profile or the repo sidebar) for the published
image.

By default a package published this way is **private**, visible only to you.
To pull it from Tower without authenticating, open the package on GitHub →
**Package settings** → change visibility to public, or see "Private image"
below to keep it private and authenticate instead.

`.gitignore` already excludes `config.json` (your real API keys) — only
`config.example.json` gets committed.

## Installing on Tower (from GHCR)

Once Actions has built the image, SSH into Tower:

```bash
docker pull ghcr.io/<your-username>/anime-tracker:latest

mkdir -p /mnt/user/appdata/anime-tracker/output

curl -sL -o /mnt/user/appdata/anime-tracker/anime_ids.json \
  https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/master/anime_ids.json

docker run -d \
  --name anime-tracker \
  --restart unless-stopped \
  -p 8080:8080 \
  -v /mnt/user/appdata/anime-tracker:/config \
  -v /mnt/user/appdata/anime-tracker/output:/output \
  -e PUID=99 -e PGID=100 -e TZ=Australia/Hobart \
  ghcr.io/<your-username>/anime-tracker:latest
```

First run writes a `config.json` template and exits — edit it with your real
URLs and API keys, then `docker restart anime-tracker`.

Or point `docker-compose.yml`'s `image:` at the GHCR tag instead of building
locally.

### Private image

If you kept the package private, authenticate Tower once with a
[Personal Access Token](https://github.com/settings/tokens) (classic, with
`read:packages` scope):

```bash
echo "<your-PAT>" | docker login ghcr.io -u <your-username> --password-stdin
```

Then `docker pull` works as above.

### Updating

Push a change to `main`, wait for Actions to finish, then on Tower:

```bash
docker pull ghcr.io/<your-username>/anime-tracker:latest
docker stop anime-tracker && docker rm anime-tracker
# re-run the docker run command above
```

(Or `docker compose pull && docker compose up -d` if using Compose.)

## Building locally instead

If you'd rather not use GitHub at all:

```bash
docker build -t anime-tracker:latest .
docker compose up -d
```

### Unraid

Add a container manually, or use the Docker tab with:

- Repository: `anime-tracker:latest`
- Port: `8080` → `8080`
- Path: `/mnt/user/appdata/anime-tracker` → `/config`
- Path: `/mnt/user/appdata/anime-tracker/output` → `/output`
- Variables: `PUID=99`, `PGID=100`, `TZ=Australia/Hobart`

## First run

1. Start the container. It writes a `config.json` template to `/config` and
   exits with instructions in the log.
2. Edit `/mnt/user/appdata/anime-tracker/config.json`:

```json
{
  "ShokoURL": "http://192.168.5.145:8111",
  "APIKey": "your-shoko-api-key",
  "MappingFile": "/config/anime_ids.json",
  "SonarrURL": "http://192.168.5.145:8989",
  "SonarrAPIKey": "your-sonarr-api-key"
}
```

3. Put the Kometa-Team Anime-IDs mapping at
   `/mnt/user/appdata/anime-tracker/anime_ids.json`
   (https://github.com/Kometa-Team/Anime-IDs).
4. Start it again.

`MappingFile` must be the **container** path (`/config/anime_ids.json`), not an
Unraid host path.

Sonarr is optional — leave those two keys out and the migration section is
skipped entirely.

## Access

- Dashboard: `http://<unraid-ip>:8080/`
- CSVs: same port (`/missing.csv`, `/sonarr_remaining.csv`, etc.)
- Logs: `docker logs anime-tracker`

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PUID` / `PGID` | `99` / `100` | Unraid `nobody:users` |
| `TZ` | `Australia/Hobart` | Timezone for schedule and timestamps |
| `CRON_SCHEDULE` | `0 4 * * *` | Standard cron expression |
| `RUN_ON_START` | `true` | Run once immediately on start |
| `SERVE_WEB` | `true` | Serve the dashboard |
| `WEB_PORT` | `8080` | Dashboard port |
| `MAX_RESULTS` | `1000` | How many AniList TV series to track |
| `MIN_POPULARITY` | `50000` | Skip series below this AniList user count |
| `CACHE_MAX_AGE_HOURS` | `24` | AniList cache lifetime |
| `RUN_ONCE` | unset | Run once and exit (no scheduler/server) |

## One-off run

```bash
docker compose run --rm anime-tracker python3 /app/main.py --once
```

## Notes

- Scheduling is built in (`croniter`), so there's no cron daemon in the image.
- If AniList is unreachable, a stale cache is used rather than failing the run.
- Ownership matches on MAL **or** AniDB ID; Sonarr matches on TVDB ID. Each is
  read from Shoko's `IDs` object with a fallback to the `Links` list, since the
  shape varies by Shoko version.
- Shoko's per-series episode count path also varies by version. If the totals
  show 0, the log says so explicitly.
