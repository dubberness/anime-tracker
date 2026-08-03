"""Shared HTTP plumbing: retry with exponential backoff."""

import time

import requests

from logging_setup import get_logger

log = get_logger(__name__)


class ApiError(Exception):
    """Raised when a request keeps failing after every retry."""


def _is_retryable(exc):
    """Client errors won't fix themselves - only retry transport/5xx faults."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        # 429 is worth waiting out; AniList uses it liberally.
        return status == 429 or status >= 500
    return True


def request_with_retry(method, url, *, max_retries=4, backoff=2,
                       timeout=60, **kwargs):
    """Issue a request, retrying transient failures with exponential backoff."""
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()
        except Exception as exc:  # noqa: BLE001 - classified by _is_retryable
            last_error = exc

            if attempt >= max_retries or not _is_retryable(exc):
                break

            delay = backoff * (2 ** (attempt - 1))
            log.warning(
                "Request failed (attempt %s/%s), retrying in %ss: %s",
                attempt, max_retries, delay, exc,
            )
            time.sleep(delay)

    raise ApiError(f"{method} {url} failed after {max_retries} attempts: {last_error}")


def describe_error(exc):
    """Turn an exception into something worth showing in the settings UI."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        if code in (401, 403):
            return "Authentication failed - check the API key"
        if code == 404:
            return "Endpoint not found - check the URL"
        return f"HTTP {code}"
    if isinstance(exc, requests.ConnectionError):
        return "Could not connect - check the URL and that the service is running"
    if isinstance(exc, requests.Timeout):
        return "Timed out"
    return str(exc)
