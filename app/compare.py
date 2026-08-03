"""Library comparison: ID extraction, ownership matching, stats and diffs."""

import json
import math
import os
import re
from dataclasses import dataclass, field, asdict

ANIDB_URL_RE = re.compile(r"/anime/(\d+)")
TVDB_URL_RE = re.compile(r"/series/(\d+)")
MAL_URL_RE = re.compile(r"/anime/(\d+)")

GB = 1024 ** 3


@dataclass
class Entry:
    rank: int
    title: str
    score: int
    popularity: int
    recommendation_score: float
    episodes: object
    year: object
    anilist_id: int
    mal_id: str
    anidb_id: str
    image: str
    owned: bool
    is_franchise_root: bool


@dataclass
class SonarrEntry:
    title: str
    tvdb_id: object
    status: str
    episode_file_count: int
    episode_count: int
    size_gb: float
    migrated: bool


@dataclass
class Diff:
    has_previous: bool = False
    newly_owned: list = field(default_factory=list)
    newly_tracked: list = field(default_factory=list)


# ==========================
# Mapping file
# ==========================

def load_mappings(path):
    """Build an AniList ID -> mapping lookup from the Kometa Anime-IDs file."""
    print("Loading Anime ID mappings...")

    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    lookup = {}

    for value in raw.values():
        if not isinstance(value, dict):
            continue
        anilist_id = value.get("anilist_id")
        if anilist_id:
            lookup[int(anilist_id)] = value

    print(f"Mappings loaded: {len(lookup)}")
    return lookup


# ==========================
# Shoko ID extraction
# ==========================

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

        for value in _as_list(ids.get("TvDB")):
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

    print(f"Shoko IDs - MAL: {len(mal_ids)}, "
          f"AniDB: {len(anidb_ids)}, TVDB: {len(tvdb_ids)}")

    return mal_ids, anidb_ids, tvdb_ids


def count_shoko_episodes(shoko_series):
    """Total local episodes across the Shoko library.

    The property path varies by Shoko version, so several known shapes are
    tried before giving up.
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

    if shoko_series and total == 0:
        print(f"Warning: Shoko episode count came back as 0 across "
              f"{len(shoko_series)} series - the property path likely does "
              f"not match your Shoko version.")

    return total


# ==========================
# AniList vs Shoko
# ==========================

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


def compare_collections(anilist, mappings, mal_ids, anidb_ids, cfg):
    """Match the AniList list against the Shoko library."""
    print("Comparing libraries...")

    by_mal = {}

    for media in anilist:
        mapping = mappings.get(int(media["id"]))
        if not mapping:
            continue

        mal_id = mapping.get("mal_id")
        if not mal_id:
            continue

        popularity = media.get("popularity") or 0
        if popularity < cfg.min_popularity:
            continue

        anidb_id = mapping.get("anidb_id")
        score = media.get("averageScore") or 0

        owned = (str(mal_id) in mal_ids
                 or (anidb_id and str(anidb_id) in anidb_ids))

        weighted = round(score * 0.8 + math.log10(max(popularity, 1)) * 10, 2)

        entry = Entry(
            rank=media.get("rank", 0),
            title=_title_of(media),
            score=score,
            popularity=popularity,
            recommendation_score=weighted,
            episodes=media.get("episodes"),
            year=(media.get("startDate") or {}).get("year"),
            anilist_id=media["id"],
            mal_id=str(mal_id),
            anidb_id=str(anidb_id) if anidb_id else "",
            image=(media.get("coverImage") or {}).get("large", ""),
            owned=bool(owned),
            is_franchise_root=not _has_prequel(media),
        )

        # Several AniList entries can map to one MAL ID; keep the best-ranked.
        existing = by_mal.get(entry.mal_id)
        if existing is None or entry.rank < existing.rank:
            by_mal[entry.mal_id] = entry

    results = sorted(by_mal.values(), key=lambda e: e.rank)
    print(f"Tracked entries: {len(results)}")
    return results


# ==========================
# Sonarr vs Shoko
# ==========================

def compare_sonarr(sonarr_series, tvdb_ids):
    """Work out which Sonarr series already exist in Shoko, matched on TVDB."""
    print("Comparing Sonarr against Shoko...")

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

    return results


# ==========================
# Stats
# ==========================

def _avg(values):
    values = [v for v in values if v]
    return round(sum(values) / len(values), 2) if values else 0


def build_stats(results):
    owned = [r for r in results if r.owned]
    missing = [r for r in results if not r.owned]
    total = len(results)

    return {
        "total": total,
        "owned": len(owned),
        "missing": len(missing),
        "completion": round(len(owned) / total * 100, 2) if total else 0,
        "avg_owned_score": _avg([r.score for r in owned]),
        "avg_missing_score": _avg([r.score for r in missing]),
    }


def build_tiers(results, tiers=(100, 250, 500, 1000)):
    output = []

    for tier in tiers:
        subset = [r for r in results if r.rank <= tier]
        if not subset:
            continue
        owned = [r for r in subset if r.owned]
        output.append({
            "tier": tier,
            "owned": len(owned),
            "total": len(subset),
            "completion": round(len(owned) / len(subset) * 100, 2),
        })

    return output


def build_migration_stats(sonarr_results):
    migrated = [r for r in sonarr_results if r.migrated]
    remaining = [r for r in sonarr_results if not r.migrated]
    total = len(sonarr_results)

    return {
        "total": total,
        "migrated": len(migrated),
        "remaining": len(remaining),
        "completion": round(len(migrated) / total * 100, 2) if total else 0,
        "remaining_size_gb": round(sum(r.size_gb for r in remaining), 2),
    }


def build_library_totals(shoko_series, sonarr_series):
    return {
        "shoko_shows": len(shoko_series),
        "shoko_episodes": count_shoko_episodes(shoko_series),
        "sonarr_shows": len(sonarr_series),
        "sonarr_episodes": sum(
            (s.get("statistics") or {}).get("episodeFileCount", 0) or 0
            for s in sonarr_series
        ),
    }


# ==========================
# Run-over-run diff
# ==========================

def build_diff(results, snapshot_file):
    diff = Diff()

    if not os.path.exists(snapshot_file):
        return diff

    diff.has_previous = True

    try:
        with open(snapshot_file, encoding="utf-8") as fh:
            previous = json.load(fh)
    except (json.JSONDecodeError, OSError):
        diff.has_previous = False
        return diff

    previous_lookup = {str(p.get("mal_id")): p for p in previous}

    for result in results:
        prev = previous_lookup.get(result.mal_id)

        if prev is None:
            diff.newly_tracked.append(result)
        elif result.owned and not prev.get("owned"):
            diff.newly_owned.append(result)

    return diff


def save_snapshot(results, snapshot_file):
    payload = [
        {"mal_id": r.mal_id, "title": r.title, "rank": r.rank, "owned": r.owned}
        for r in results
    ]
    with open(snapshot_file, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def entry_to_dict(entry):
    return asdict(entry)
