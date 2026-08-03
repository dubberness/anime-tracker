"""Sonarr client."""

from clients.base import request_with_retry
from logging_setup import get_logger

log = get_logger(__name__)


class SonarrClient:
    def __init__(self, settings, network):
        self.settings = settings
        self.network = network

    @property
    def _headers(self):
        return {"X-Api-Key": self.settings.api_key}

    def fetch_series(self):
        log.info("Loading Sonarr library from %s", self.settings.url)

        data = request_with_retry(
            "GET", f"{self.settings.url}/api/v3/series",
            max_retries=self.network.max_retries,
            backoff=self.network.initial_backoff_seconds,
            timeout=self.network.timeout_seconds,
            headers=self._headers,
        )

        log.info("Sonarr series loaded: %s", len(data))
        return data

    def test_connection(self):
        data = request_with_retry(
            "GET", f"{self.settings.url}/api/v3/system/status",
            max_retries=1,
            timeout=15,
            headers=self._headers,
        )
        version = (data or {}).get("version", "")
        return f"Connected to Sonarr {version}".strip()
