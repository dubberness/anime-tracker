"""AniList GraphQL client, with an on-disk cache and stale-cache fallback."""

import json
import os
import time
from datetime import datetime, timedelta

from clients.base import request_with_retry
from logging_setup import get_logger

log = get_logger(__name__)

MEDIA_FRAGMENT = """
fragment trackedMedia on Media {
  id
  title { romaji english }
  format
  averageScore
  popularity
  episodes
  genres
  status
  season
  seasonYear
  nextAiringEpisode { episode airingAt }
  startDate { year }
  coverImage { large }
  relations {
    edges {
      relationType(version: 2)
      node { id type format }
    }
  }
}
"""

QUERY = """
query ($page: Int, $perPage: Int, $formats: [MediaFormat], $sort: [MediaSort]) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(type: ANIME, format_in: $formats, sort: $sort) { ...trackedMedia }
  }
}
""" + MEDIA_FRAGMENT

# Everything AniList currently marks as airing, regardless of which season it
# started in. A season query cannot answer this: AniList tags media with its
# *start* season, so a two-cour Spring show is invisible in the Summer chart
# even though it is still going out weekly.
AIRING_QUERY = """
query ($page: Int, $perPage: Int, $formats: [MediaFormat]) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(type: ANIME, status: RELEASING, format_in: $formats,
          sort: [POPULARITY_DESC]) { ...trackedMedia }
  }
}
""" + MEDIA_FRAGMENT

# The three rankings the seasons page toggles between. These double as the
# GraphQL aliases below, so one request per season covers all three.
SEASON_SORTS = ("popularity", "trending", "score")

SEASON_QUERY = """
query ($season: MediaSeason, $year: Int, $perPage: Int, $formats: [MediaFormat]) {
  popularity: Page(page: 1, perPage: $perPage) {
    media(type: ANIME, season: $season, seasonYear: $year,
          format_in: $formats, sort: [POPULARITY_DESC]) { ...trackedMedia }
  }
  trending: Page(page: 1, perPage: $perPage) {
    media(type: ANIME, season: $season, seasonYear: $year,
          format_in: $formats, sort: [TRENDING_DESC]) { ...trackedMedia }
  }
  score: Page(page: 1, perPage: $perPage) {
    media(type: ANIME, season: $season, seasonYear: $year,
          format_in: $formats, sort: [SCORE_DESC]) { ...trackedMedia }
  }
}
""" + MEDIA_FRAGMENT


class AniListClient:
    def __init__(self, settings, cache_file, network):
        self.settings = settings
        self.cache_file = cache_file
        self.network = network

    # -- cache --

    def cache_age(self):
        if not os.path.exists(self.cache_file):
            return None
        return datetime.now() - datetime.fromtimestamp(
            os.path.getmtime(self.cache_file)
        )

    def _read_cache(self):
        with open(self.cache_file, encoding="utf-8") as fh:
            return json.load(fh)

    def _write_cache(self, entries):
        tmp = self.cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
        os.replace(tmp, self.cache_file)

    # -- fetch --

    def fetch(self, force=False, progress=None):
        """Return the ranked media list, preferring a fresh cache."""
        age = self.cache_age()
        max_age = timedelta(hours=self.settings.cache_max_age_hours)

        if not force and age is not None and age < max_age:
            hours = round(age.total_seconds() / 3600, 1)
            log.info("Using cached AniList data (%sh old)", hours)
            try:
                return self._read_cache()
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Cache unreadable (%s) - refetching", exc)

        log.info("Downloading AniList list (%s, %s)",
                 "/".join(self.settings.formats), self.settings.sort)

        try:
            entries = self._download(progress=progress)
        except Exception as exc:  # noqa: BLE001 - fall back to stale data
            if os.path.exists(self.cache_file):
                log.error("AniList download failed (%s) - using stale cache", exc)
                return self._read_cache()
            raise

        self._write_cache(entries)
        return entries

    def _download(self, progress=None):
        entries = []
        page = 1
        rank = 1

        while True:
            if progress:
                progress(f"AniList page {page} ({len(entries)} entries)")

            payload = {
                "query": QUERY,
                "variables": {
                    "page": page,
                    "perPage": self.settings.page_size,
                    "formats": self.settings.formats,
                    "sort": [self.settings.sort],
                },
            }

            data = request_with_retry(
                "POST", self.settings.url,
                max_retries=self.network.max_retries,
                backoff=self.network.initial_backoff_seconds,
                timeout=self.network.timeout_seconds,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if data.get("errors") and not data.get("data"):
                raise RuntimeError(f"AniList returned errors: {data['errors']}")

            page_data = data["data"]["Page"]
            media = page_data.get("media") or []

            for item in media:
                item["rank"] = rank
                rank += 1

            entries.extend(media)

            if (not page_data["pageInfo"]["hasNextPage"]
                    or not media
                    or len(entries) >= self.settings.max_results):
                break

            page += 1
            time.sleep(self.settings.request_delay_ms / 1000)

        entries = entries[: self.settings.max_results]
        log.info("AniList entries loaded: %s", len(entries))
        return entries

    def fetch_airing(self, progress=None):
        """Everything AniList currently marks RELEASING, most popular first.

        Deliberately uncached, like fetch_season: the whole point is knowing
        what is going out *now*, and a day-old answer is how episodes get
        missed in the first place.

        The popularity floor that compare_collections applies is deliberately
        not used here - a show three episodes into its first cour has barely
        any popularity yet, and those are exactly the ones worth catching.
        """
        entries = []
        page = 1
        rank = 1

        while True:
            if progress:
                progress(f"Airing page {page} ({len(entries)} shows)")

            payload = {
                "query": AIRING_QUERY,
                "variables": {
                    "page": page,
                    "perPage": self.settings.page_size,
                    "formats": self.settings.formats,
                },
            }

            data = request_with_retry(
                "POST", self.settings.url,
                max_retries=self.network.max_retries,
                backoff=self.network.initial_backoff_seconds,
                timeout=self.network.timeout_seconds,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if data.get("errors") and not data.get("data"):
                raise RuntimeError(f"AniList returned errors: {data['errors']}")

            page_data = data["data"]["Page"]
            media = page_data.get("media") or []

            for item in media:
                item["rank"] = rank
                rank += 1

            entries.extend(media)

            if (not page_data["pageInfo"]["hasNextPage"]
                    or not media
                    or len(entries) >= self.settings.airing_max_results):
                break

            page += 1
            time.sleep(self.settings.request_delay_ms / 1000)

        entries = entries[: self.settings.airing_max_results]
        log.info("Airing shows loaded: %s", len(entries))
        return entries

    def fetch_season(self, season, year, per_page=20):
        """Top `per_page` for one season, under each of SEASON_SORTS.

        Deliberately uncached: it is one request for all three rankings, and
        the current season moves under you in a way the main list does not.
        """
        payload = {
            "query": SEASON_QUERY,
            "variables": {
                "season": season,
                "year": year,
                "perPage": per_page,
                "formats": self.settings.formats,
            },
        }

        data = request_with_retry(
            "POST", self.settings.url,
            max_retries=self.network.max_retries,
            backoff=self.network.initial_backoff_seconds,
            timeout=self.network.timeout_seconds,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        if data.get("errors") and not data.get("data"):
            raise RuntimeError(f"AniList returned errors: {data['errors']}")

        body = data.get("data") or {}
        return {
            key: (body.get(key) or {}).get("media") or []
            for key in SEASON_SORTS
        }

    def test_connection(self):
        """Cheap one-item query used by the settings page."""
        payload = {
            "query": (
                "query { Page(page: 1, perPage: 1) "
                "{ media(type: ANIME) { id } } }"
            )
        }
        request_with_retry(
            "POST", self.settings.url,
            max_retries=1,
            timeout=15,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        return "AniList reachable"
