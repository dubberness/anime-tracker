"""Shoko client."""

from clients.base import request_with_retry
from logging_setup import get_logger

log = get_logger(__name__)

PAGE_SIZE = 100

# Backstop for a Shoko build that ignores `page` and keeps handing back the
# same batch: the loop's only other exit is an empty response, so without this
# it spins forever and the list grows until the container is killed. At this
# page size that is a 200,000-series ceiling, well past any real library.
MAX_PAGES = 2000


class ShokoClient:
    def __init__(self, settings, network):
        self.settings = settings
        self.network = network

    @property
    def _headers(self):
        return {"apikey": self.settings.api_key}

    def fetch_series(self, progress=None):
        """Every series in the library, following pagination."""
        log.info("Loading Shoko library from %s", self.settings.url)

        series = []
        page = 1
        total = None

        while True:
            data = request_with_retry(
                "GET", f"{self.settings.url}/api/v3/series",
                max_retries=self.network.max_retries,
                backoff=self.network.initial_backoff_seconds,
                timeout=self.network.timeout_seconds,
                params={"page": page, "pageSize": PAGE_SIZE},
                headers=self._headers,
            )

            if isinstance(data, dict):
                batch = data.get("List", [])
                if total is None:
                    total = data.get("Total")
            else:
                batch = data

            if not batch:
                break

            series.extend(batch)
            if progress:
                progress(f"Shoko page {page} ({len(series)} series)")

            # Shoko reports the library size on every page, so the count is the
            # cheapest exit - an empty final page is one wasted request, and on
            # a build that ignores `page` it is the only exit that ever fires.
            if total is not None and len(series) >= total:
                break

            if page >= MAX_PAGES:
                log.warning(
                    "Stopped reading Shoko at %s pages (%s series) - it kept "
                    "returning data past the library size it reported",
                    page, len(series),
                )
                break

            page += 1

        log.info("Shoko series loaded: %s", len(series))
        return series

    def sample_series(self):
        """One raw series - powers the ID-shape diagnostics in settings."""
        data = request_with_retry(
            "GET", f"{self.settings.url}/api/v3/series",
            max_retries=1,
            timeout=20,
            params={"page": 1, "pageSize": 1},
            headers=self._headers,
        )
        batch = data.get("List", []) if isinstance(data, dict) else data
        return batch[0] if batch else None

    def test_connection(self):
        data = request_with_retry(
            "GET", f"{self.settings.url}/api/v3/series",
            max_retries=1,
            timeout=15,
            params={"page": 1, "pageSize": 1},
            headers=self._headers,
        )
        total = data.get("Total") if isinstance(data, dict) else None
        if total is not None:
            return f"Connected - {total} series in the library"
        return "Connected"
