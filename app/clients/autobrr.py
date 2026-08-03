"""Autobrr client.

Only two calls are needed: one to prove the connection works, and one to tell
autobrr to re-read the title list now rather than waiting out its own six-hour
poll. Everything else about the filter - indexers, quality, release groups,
download client - stays configured in autobrr itself.
"""

from clients.base import request_with_retry
from logging_setup import get_logger

log = get_logger(__name__)


class AutobrrClient:
    def __init__(self, settings, network):
        self.settings = settings
        self.network = network

    @property
    def _headers(self):
        return {"X-API-Token": self.settings.api_key}

    def trigger_list_refresh(self):
        """Ask autobrr to re-poll the list feeding its filter."""
        log.info("Triggering autobrr refresh for list %s", self.settings.list_id)

        request_with_retry(
            "POST",
            f"{self.settings.url}/api/webhook/lists/trigger/{self.settings.list_id}",
            max_retries=self.network.max_retries,
            backoff=self.network.initial_backoff_seconds,
            timeout=self.network.timeout_seconds,
            headers=self._headers,
        )

    def test_connection(self):
        """Listing filters proves both reachability and a valid API key."""
        data = request_with_retry(
            "GET", f"{self.settings.url}/api/filters",
            max_retries=1,
            timeout=15,
            headers=self._headers,
        )
        count = len(data) if isinstance(data, list) else 0
        return f"Connected to autobrr - {count} filters found"
