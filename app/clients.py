"""API clients for AniList, Shoko and Sonarr, with retry/backoff."""

import json
import os
import time
from datetime import datetime, timedelta

import requests

ANILIST_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(type: ANIME, format: TV, sort: POPULARITY_DESC) {
      id
      title { romaji english }
      format
      averageScore
      popularity
      episodes
      startDate { year }
      coverImage { large }
      relations {
        edges {
          relationType(version: 2)
          node { id type }
        }
      }
    }
  }
}
"""


class ApiError(Exception):
    pass


def request_with_retry(method, url, *, max_retries=4, backoff=2, **kwargs):
    """Issue a request, retrying transient failures with exponential backoff."""
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.request(method, url, timeout=60, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - retry on anything transient
            last_error = exc

            if attempt >= max_retries:
                break

            delay = backoff * (2 ** (attempt - 1))
            print(f"  Request failed (attempt {attempt}/{max_retries}) - "
                  f"retrying in {delay}s: {exc}")
            time.sleep(delay)

    raise ApiError(f"Request failed after {max_retries} attempts: "
                   f"{url} ({last_error})")


# ==========================
# AniList
# ==========================

def fetch_anilist(cfg):
    """Fetch the AniList Top TV list, using a local cache when it is fresh."""
    if os.path.exists(cfg.cache_file):
        age = datetime.now() - datetime.fromtimestamp(
            os.path.getmtime(cfg.cache_file)
        )
        if age < timedelta(hours=cfg.cache_max_age_hours):
            hours = round(age.total_seconds() / 3600, 1)
            print(f"Using cached AniList data ({hours}h old)")
            with open(cfg.cache_file, encoding="utf-8") as fh:
                return json.load(fh)

    print("Downloading AniList Top TV...")

    try:
        entries = []
        page = 1
        rank = 1

        while True:
            print(f"  AniList page {page}")

            payload = {
                "query": ANILIST_QUERY,
                "variables": {"page": page, "perPage": cfg.page_size},
            }

            data = request_with_retry(
                "POST", cfg.anilist_url,
                max_retries=cfg.max_retries,
                backoff=cfg.initial_backoff_seconds,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if "errors" in data and not data.get("data"):
                raise ApiError(f"AniList returned errors: {data['errors']}")

            page_data = data["data"]["Page"]
            media = page_data["media"]

            for item in media:
                item["rank"] = rank
                rank += 1

            entries.extend(media)

            if (not page_data["pageInfo"]["hasNextPage"]
                    or not media
                    or len(entries) >= cfg.max_results):
                break

            page += 1
            time.sleep(cfg.request_delay_ms / 1000)

        entries = entries[: cfg.max_results]

        print(f"AniList TV entries loaded: {len(entries)}")

        with open(cfg.cache_file, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)

        return entries

    except Exception as exc:  # noqa: BLE001
        if os.path.exists(cfg.cache_file):
            print(f"AniList download failed - falling back to existing cache "
                  f"(may be stale): {exc}")
            with open(cfg.cache_file, encoding="utf-8") as fh:
                return json.load(fh)
        raise


# ==========================
# Shoko
# ==========================

def fetch_shoko(cfg):
    """Fetch every series from Shoko, following pagination."""
    print("Loading Shoko library...")

    series = []
    page = 1

    while True:
        data = request_with_retry(
            "GET", f"{cfg.shoko_url}/api/v3/series",
            max_retries=cfg.max_retries,
            backoff=cfg.initial_backoff_seconds,
            params={"page": page, "pageSize": 100},
            headers={"apikey": cfg.shoko_api_key},
        )

        batch = data.get("List", []) if isinstance(data, dict) else data

        if not batch:
            break

        series.extend(batch)
        print(f"  Shoko page {page} - {len(batch)}")
        page += 1

    print(f"Shoko series loaded: {len(series)}")
    return series


# ==========================
# Sonarr
# ==========================

def fetch_sonarr(cfg):
    """Fetch every series from Sonarr."""
    print("Loading Sonarr library...")

    data = request_with_retry(
        "GET", f"{cfg.sonarr_url}/api/v3/series",
        max_retries=cfg.max_retries,
        backoff=cfg.initial_backoff_seconds,
        headers={"X-Api-Key": cfg.sonarr_api_key},
    )

    print(f"Sonarr series loaded: {len(data)}")
    return data
