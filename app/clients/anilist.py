"""AniList GraphQL client, with an on-disk cache and stale-cache fallback."""

import json
import os
import time
from datetime import datetime, timedelta

from clients.base import request_with_retry
from logging_setup import get_logger

log = get_logger(__name__)

QUERY = """
query ($page: Int, $perPage: Int, $formats: [MediaFormat], $sort: [MediaSort]) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(type: ANIME, format_in: $formats, sort: $sort) {
      id
      title { romaji english }
      format
      averageScore
      popularity
      episodes
      genres
      status
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
