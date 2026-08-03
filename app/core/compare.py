"""ID extraction and ownership matching.

Pure functions over already-fetched data - no HTTP, no disk - so the matching
rules can be unit tested directly.
"""

import math
import re

from core.models import Entry, SonarrEntry
from logging_setup import get_logger

log = get_logger(__name__)

ANIDB_URL_RE = re.compile(r"/anime/(\d+)")
TVDB_URL_RE = re.compile(r"/series/(\d+)")
MAL_URL_RE = re.compile(r"/anime/(\d+)")

GB = 1024 ** 3


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def extract_shoko_ids(shoko_series):
    """Pull MAL, AniDB and TVDB IDs out of the Shoko library.

    Shoko exposes IDs differently across versions - IDs.AniDB / IDs.TvDB on
    newer builds, a Links list on others. Both paths are checked so this keeps
    working either way.
    """
    mal_ids, anidb_ids, tvdb_ids = set(), set(), set()

    for series in shoko_series:
        ids = series.get("IDs") or {}

        for value in _as_list(ids.get("AniDB")):
            if value:
                anidb_ids.add(str(value))

        for value in _as_list(ids.get("TvDB")) + _as_list(ids.get("TvDBID")):
            if value:
                tvdb_ids.add(str(value))

        for value in _as_list(ids.get("MAL")) + _as_list(ids.get("MyAnimeList")):
            if value:
                mal_ids.add(str(value))

        for link in series.get("Links") or []:
            name = (link.get("Name") or "").lower()
            url = link.get("URL") or ""

            if name in ("myanimelist", "mal"):
                match = MAL_URL_RE.search(url)
                if match:
                    mal_ids.add(match.group(1))
            elif name == "anidb":
                match = ANIDB_URL_RE.search(url)
                if match:
                    anidb_ids.add(match.group(1))
            elif name in ("thetvdb", "tvdb"):
                match = TVDB_URL_RE.search(url)
                if match:
                    tvdb_ids.add(match.group(1))

    log.info("Shoko IDs - MAL: %s, AniDB: %s, TVDB: %s",
             len(mal_ids), len(anidb_ids), len(tvdb_ids))

    return mal_ids, anidb_ids, tvdb_ids


def count_shoko_episodes(shoko_series):
    """Total local episodes across the library.

    The property path varies by Shoko version, so several known shapes are
    tried. Returns (total, looks_wrong) - the caller surfaces the warning.
    """
    total = 0

    for series in shoko_series:
        sizes = series.get("Sizes") or {}
        local = sizes.get("Local") or {}

        value = (local.get("Episodes")
                 or local.get("Total")
                 or series.get("EpisodeCount")
                 or 0)

        if isinstance(value, dict):
            value = value.get("Episodes", 0) or 0

        try:
            total += int(value)
        except (TypeError, ValueError):
            continue

    looks_wrong = bool(shoko_series) and total == 0
    if looks_wrong:
        log.warning(
            "Shoko episode count came back as 0 across %s series - the property "
            "path likely does not match your Shoko version",
            len(shoko_series),
        )

    return total, looks_wrong


def _title_of(media):
    title = media.get("title") or {}
    return title.get("english") or title.get("romaji") or "Unknown"


def _has_prequel(media):
    relations = media.get("relations") or {}
    for edge in relations.get("edges") or []:
        node = edge.get("node") or {}
        if edge.get("relationType") == "PREQUEL" and node.get("type") == "ANIME":
            return True
    return False


def recommendation_score(score, popularity):
    """Blend rating with reach so a niche 90 doesn't outrank a popular 85."""
    return round(score * 0.8 + math.log10(max(popularity, 1)) * 10, 2)


def compare_collections(anilist, mappings, mal_ids, anidb_ids, settings):
    """Match the AniList list against the Shoko library."""
    log.info("Comparing %s AniList entries against the Shoko library", len(anilist))

    by_mal = {}
    unmapped = 0

    for media in anilist:
        mapping = mappings.get(int(media["id"]))
        if not mapping:
            unmapped += 1
            continue

        mal_id = mapping.get("mal_id")
        if not mal_id:
            unmapped += 1
            continue

        popularity = media.get("popularity") or 0
        if popularity < settings.min_popularity:
            continue

        anidb_id = mapping.get("anidb_id")
        score = media.get("averageScore") or 0

        owned = (str(mal_id) in mal_ids
                 or (anidb_id and str(anidb_id) in anidb_ids))

        entry = Entry(
            rank=media.get("rank", 0),
            title=_title_of(media),
            score=score,
            popularity=popularity,
            recommendation_score=recommendation_score(score, popularity),
            episodes=media.get("episodes"),
            year=(media.get("startDate") or {}).get("year"),
            anilist_id=media["id"],
            mal_id=str(mal_id),
            anidb_id=str(anidb_id) if anidb_id else "",
            image=(media.get("coverImage") or {}).get("large", ""),
            owned=bool(owned),
            is_franchise_root=not _has_prequel(media),
            format=media.get("format") or "",
            status=media.get("status") or "",
            genres=media.get("genres") or [],
        )

        # Several AniList entries can map to one MAL ID; keep the best-ranked.
        existing = by_mal.get(entry.mal_id)
        if existing is None or entry.rank < existing.rank:
            by_mal[entry.mal_id] = entry

    results = sorted(by_mal.values(), key=lambda e: e.rank)
    log.info("Tracked entries: %s (%s AniList entries had no usable mapping)",
             len(results), unmapped)
    return results


def compare_sonarr(sonarr_series, tvdb_ids):
    """Work out which Sonarr series already exist in Shoko, matched on TVDB."""
    results = []

    for series in sonarr_series:
        stats = series.get("statistics") or {}
        tvdb_id = series.get("tvdbId")

        results.append(SonarrEntry(
            title=series.get("title", "Unknown"),
            tvdb_id=tvdb_id,
            status=series.get("status", ""),
            episode_file_count=stats.get("episodeFileCount", 0) or 0,
            episode_count=stats.get("episodeCount", 0) or 0,
            size_gb=round((stats.get("sizeOnDisk", 0) or 0) / GB, 2),
            migrated=bool(tvdb_id and str(tvdb_id) in tvdb_ids),
        ))

    log.info("Sonarr comparison: %s series, %s already in Shoko",
             len(results), sum(1 for r in results if r.migrated))
    return results
