"""Kometa Anime-IDs mapping file: download, refresh and lookup.

The mapping is what bridges an AniList ID to the MAL/AniDB IDs Shoko knows
about. Previously this had to be fetched by hand before the app would start;
it now downloads and refreshes itself.
"""

import json
import os
import time
from datetime import datetime, timedelta

import requests

from logging_setup import get_logger

log = get_logger(__name__)


class MappingError(Exception):
    pass


def file_age(path):
    if not os.path.exists(path):
        return None
    return datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))


def download(url, path, timeout=60):
    """Fetch the mapping file to `path`, atomically."""
    log.info("Downloading anime ID mappings from %s", url)

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()

    # Parse before replacing so a truncated download can't poison the file.
    payload = response.json()
    if not isinstance(payload, dict) or not payload:
        raise MappingError("Mapping download did not look like the expected JSON object")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)

    log.info("Mapping file saved to %s (%s entries)", path, len(payload))
    return payload


def ensure(settings, force=False):
    """Make sure a usable mapping file exists, downloading it if allowed."""
    path = settings.path
    age = file_age(path)
    stale = age is not None and age > timedelta(days=settings.max_age_days)

    if force or age is None or stale:
        if not settings.auto_download:
            if age is None:
                raise MappingError(
                    f"No mapping file at {path} and auto-download is off. "
                    f"Download it from {settings.url} or turn auto-download on."
                )
            log.info("Mapping file is stale but auto-download is off - using it anyway")
        else:
            try:
                return download(settings.url, path)
            except Exception as exc:
                if age is None:
                    raise MappingError(
                        f"Could not download the mapping file: {exc}"
                    ) from exc
                log.warning("Mapping refresh failed (%s) - using the existing file", exc)

    return None


def load(settings, force_download=False):
    """Return an AniList ID -> mapping lookup."""
    ensure(settings, force=force_download)

    path = settings.path
    if not os.path.exists(path):
        raise MappingError(f"Mapping file not found: {path}")

    started = time.time()
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    lookup = {}
    for value in raw.values():
        if not isinstance(value, dict):
            continue
        anilist_id = value.get("anilist_id")
        if not anilist_id:
            continue
        try:
            lookup[int(anilist_id)] = value
        except (TypeError, ValueError):
            continue

    log.info("Mappings loaded: %s AniList IDs in %.2fs",
             len(lookup), time.time() - started)

    if not lookup:
        raise MappingError(
            f"{path} parsed but contained no anilist_id entries - "
            f"is it really the Kometa Anime-IDs file?"
        )

    return lookup
