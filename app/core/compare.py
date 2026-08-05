"""ID extraction and ownership matching.

Pure functions over already-fetched data - no HTTP, no disk - so the matching
rules can be unit tested directly.
"""

import math
import re

from core.models import (
    SONARR_MISSING,
    SONARR_OWNED,
    SONARR_UNKNOWN,
    SONARR_UNMAPPED,
    SONARR_WANTED,
    Entry,
    ShokoEntry,
    SonarrEntry,
)
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


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _anidb_to_tvdb(mappings):
    """AniDB ID -> TVDB ID, reversed out of the AniList-keyed mapping file.

    Shoko only ever reports an AniDB ID for a series, never an AniList one, so
    the mapping has to be flipped to be useful as a Shoko-side lookup.
    """
    index = {}
    for entry in (mappings or {}).values():
        anidb_id = entry.get("anidb_id")
        tvdb_id = entry.get("tvdb_id")
        if anidb_id and tvdb_id:
            index[str(anidb_id)] = str(tvdb_id)
    return index


def _shoko_series_ids(series, anidb_to_tvdb):
    """MAL, AniDB and TVDB IDs for one Shoko series, as sets of strings.

    The single home of the per-version ID rules; everything that needs to read
    a Shoko series goes through here so the awkward parts below are only ever
    written once.

    Shoko exposes IDs differently across versions - IDs.AniDB / IDs.TvDB on
    newer builds, a Links list on others. Both paths are checked so this keeps
    working either way.

    Shoko's own TVDB field can't be trusted on its own: Shoko dropped TheTVDB
    as a metadata source in favour of TMDB, so that field is just a passive
    AniDB crossref, and AniDB's crossref data itself is wrong for some titles
    (e.g. Naruto Shippuuden inheriting the original Naruto's TVDB ID). The
    series' AniDB ID - which Shoko does get right, since that's its actual
    source of truth - is cross-checked against the community-maintained
    mapping file, and any TVDB ID found there is added on top. That is why a
    single series can legitimately end up with two TVDB IDs.
    """
    ids = series.get("IDs") or {}
    mal_ids, tvdb_ids = set(), set()
    anidb_ids = {str(v) for v in _as_list(ids.get("AniDB")) if v}

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

    # After the links, so AniDB IDs that only appear there are cross-checked too.
    for anidb_id in anidb_ids:
        mapped_tvdb = anidb_to_tvdb.get(anidb_id)
        if mapped_tvdb:
            tvdb_ids.add(mapped_tvdb)

    return mal_ids, anidb_ids, tvdb_ids


def shoko_title(series):
    return series.get("Name") or series.get("Title") or "Unknown"


def shoko_episode_count(series):
    """Local episodes for one Shoko series.

    The property path varies by Shoko version, so several known shapes are
    tried. Anything unreadable counts as zero rather than raising - one odd
    series shouldn't cost the whole library total.
    """
    sizes = series.get("Sizes") or {}
    local = sizes.get("Local") or {}

    value = (local.get("Episodes")
             or local.get("Total")
             or series.get("EpisodeCount")
             or 0)

    if isinstance(value, dict):
        value = value.get("Episodes", 0) or 0

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_shoko_index(shoko_series, mappings=None):
    """One walk of the Shoko library -> (entries, mal_ids, anidb_ids, tvdb_ids).

    The three sets are what ownership matching runs on; the entries are what
    the migration page needs to show the Shoko side of the comparison. Both
    come out of the same pass so the two views can never disagree.

    Entries come back with no Sonarr status - Sonarr is read after Shoko, so
    `annotate_shoko_sonarr` fills that in once it is available.
    """
    anidb_to_tvdb = _anidb_to_tvdb(mappings)

    entries = []
    mal_ids, anidb_ids, tvdb_ids = set(), set(), set()

    for series in shoko_series:
        series_mal, series_anidb, series_tvdb = _shoko_series_ids(series, anidb_to_tvdb)

        mal_ids |= series_mal
        anidb_ids |= series_anidb
        tvdb_ids |= series_tvdb

        entries.append(ShokoEntry(
            title=shoko_title(series),
            anidb_id=sorted(series_anidb)[0] if series_anidb else "",
            tvdb_ids=sorted(series_tvdb),
            tvdb_id=sorted(series_tvdb)[0] if series_tvdb else "",
            episodes=shoko_episode_count(series),
        ))

    log.info("Shoko IDs - MAL: %s, AniDB: %s, TVDB: %s",
             len(mal_ids), len(anidb_ids), len(tvdb_ids))

    return entries, mal_ids, anidb_ids, tvdb_ids


def extract_shoko_ids(shoko_series, mappings=None):
    """The ID sets alone, for callers that don't need the per-series rows."""
    _, mal_ids, anidb_ids, tvdb_ids = build_shoko_index(shoko_series, mappings)
    return mal_ids, anidb_ids, tvdb_ids


def count_shoko_episodes(shoko_series):
    """Total local episodes across the library.

    Returns (total, looks_wrong) - the caller surfaces the warning.
    """
    total = sum(shoko_episode_count(series) for series in shoko_series)

    looks_wrong = bool(shoko_series) and total == 0
    if looks_wrong:
        log.warning(
            "Shoko episode count came back as 0 across %s series - the property "
            "path likely does not match your Shoko version",
            len(shoko_series),
        )

    return total, looks_wrong


def annotate_shoko_sonarr(entries, sonarr_index, sonarr_available=True):
    """Fill in each Shoko row's Sonarr status, once Sonarr has been read.

    A series can carry more than one TVDB ID (see _shoko_series_ids), so every
    candidate is checked and whichever one Sonarr actually knows is the one
    recorded - otherwise a stale ID sorted ahead of the good one would report a
    migrated series as Shoko-only.
    """
    for entry in entries:
        if not sonarr_available:
            entry.sonarr_status = SONARR_UNKNOWN
            continue

        if not entry.tvdb_ids:
            entry.sonarr_status = SONARR_UNMAPPED
            continue

        matched = None
        for tvdb_id in entry.tvdb_ids:
            if str(tvdb_id) in (sonarr_index or {}):
                matched = str(tvdb_id)
                break

        if matched is None:
            entry.sonarr_status = SONARR_MISSING
            continue

        series = sonarr_index[matched]
        files = series.get("episode_file_count", 0) or 0

        entry.tvdb_id = matched
        entry.sonarr_episodes = files
        entry.sonarr_status = SONARR_OWNED if files else SONARR_WANTED

    return entries


def shoko_only(entries):
    """Shoko rows Sonarr has no files for - the ones worth listing.

    Deliberately excludes `unmapped` (no TVDB ID, so unanswerable) and
    `unknown` (Sonarr was unreachable), which between them are most of a
    library and would drown the real answers.
    """
    return [e for e in entries if e.sonarr_status in (SONARR_MISSING, SONARR_WANTED)]


def shoko_episodes_by_tvdb(entries):
    """TVDB ID -> episodes Shoko holds under it.

    Summed, because Sonarr is one row per TVDB series while Shoko is roughly
    one per anime season, so several Shoko rows can sit under one Sonarr one.
    """
    totals = {}
    for entry in entries:
        for tvdb_id in entry.tvdb_ids:
            key = str(tvdb_id)
            totals[key] = totals.get(key, 0) + entry.episodes
    return totals


def title_of(media):
    title = media.get("title") or {}
    return title.get("english") or title.get("romaji") or "Unknown"


def alt_title_of(media, primary):
    """The romaji title, when it isn't already the one being displayed."""
    romaji = (media.get("title") or {}).get("romaji") or ""
    return romaji if romaji and romaji != primary else ""


# What counts as a real earlier season. A franchise's prequel is often a short
# or a recap special, which shouldn't make the sequel look like season two of
# something you follow.
SEASON_FORMATS = ("TV", "TV_SHORT", "ONA", "MOVIE")


def _relation_ids(media, relation_type, formats=None):
    """AniList IDs of the ANIME nodes on one kind of relation edge.

    A node whose format is missing is kept: the cached AniList response
    predates that field being requested, and dropping those would silently
    turn the flag off everywhere until the cache expired.
    """
    ids = set()
    relations = media.get("relations") or {}

    for edge in relations.get("edges") or []:
        node = edge.get("node") or {}
        if edge.get("relationType") != relation_type or node.get("type") != "ANIME":
            continue
        if formats and node.get("format") and node["format"] not in formats:
            continue
        node_id = _as_int(node.get("id"))
        if node_id is not None:
            ids.add(node_id)

    return ids


def owned_anilist_ids(mappings, mal_ids, anidb_ids):
    """AniList IDs whose mapping row resolves to something Shoko already has.

    Shoko only ever reports MAL and AniDB IDs, while AniList relations are
    AniList IDs, so the two have to be brought into the same namespace before
    "is the prequel of this something I own?" can be asked at all.
    """
    owned = set()

    for anilist_id, entry in (mappings or {}).items():
        mal_id = entry.get("mal_id")
        anidb_id = entry.get("anidb_id")
        if ((mal_id and str(mal_id) in mal_ids)
                or (anidb_id and str(anidb_id) in anidb_ids)):
            owned.add(anilist_id)

    return owned


def recommendation_score(score, popularity):
    """Blend rating with reach so a niche 90 doesn't outrank a popular 85."""
    return round(score * 0.8 + math.log10(max(popularity, 1)) * 10, 2)


def build_sonarr_index(sonarr_series):
    """TVDB ID -> what Sonarr holds for it, including per-season file counts.

    Sonarr is keyed on the whole TVDB series while the mapping file is one row
    per AniDB entry (roughly, per anime season). The per-season counts are what
    stop a sequel being reported as owned just because season 1 is on disk.
    """
    index = {}

    for series in sonarr_series:
        tvdb_id = series.get("tvdbId")
        if not tvdb_id:
            continue

        stats = series.get("statistics") or {}

        seasons = {}
        for season in series.get("seasons") or []:
            number = _as_int(season.get("seasonNumber"))
            if number is None:
                continue
            season_stats = season.get("statistics") or {}
            seasons[number] = season_stats.get("episodeFileCount", 0) or 0

        index[str(tvdb_id)] = {
            "title": series.get("title", "Unknown"),
            "episode_file_count": stats.get("episodeFileCount", 0) or 0,
            "seasons": seasons,
        }

    return index


def sonarr_status(tvdb_id, tvdb_season, index, available=True):
    """Where one tracked entry stands in Sonarr. See core.models for the states."""
    if not available:
        return SONARR_UNKNOWN
    if not tvdb_id:
        return SONARR_UNMAPPED

    series = (index or {}).get(str(tvdb_id))
    if series is None:
        return SONARR_MISSING

    # tvdb_season is -1 when the mapping doesn't know which season this is, in
    # which case the series-wide count is the best available answer.
    seasons = series.get("seasons") or {}
    if tvdb_season is not None and tvdb_season in seasons:
        files = seasons[tvdb_season]
    else:
        files = series.get("episode_file_count", 0)

    return SONARR_OWNED if files else SONARR_WANTED


def _build_entry(media, mapping, mal_ids, anidb_ids,
                 sonarr_index=None, sonarr_available=False, rank=None,
                 owned_ids=None):
    """Turn one AniList media object plus its mapping row into an Entry."""
    mapping = mapping or {}

    mal_id = mapping.get("mal_id")
    anidb_id = mapping.get("anidb_id")
    tvdb_id = mapping.get("tvdb_id")
    tvdb_season = _as_int(mapping.get("tvdb_season"))

    score = media.get("averageScore") or 0
    popularity = media.get("popularity") or 0

    owned = bool(
        (mal_id and str(mal_id) in mal_ids)
        or (anidb_id and str(anidb_id) in anidb_ids)
    )

    title = title_of(media)

    # Two readings of the same edges, and they differ on purpose. Being a
    # franchise root means having no earlier entry of any kind, so it stays
    # unfiltered; "a new season of something you own" only wants prequels that
    # are themselves a season, not the recap special.
    prequel_ids = _relation_ids(media, "PREQUEL")
    season_prequel_ids = _relation_ids(media, "PREQUEL", SEASON_FORMATS)

    return Entry(
        rank=media.get("rank", 0) if rank is None else rank,
        title=title,
        title_alt=alt_title_of(media, title),
        score=score,
        popularity=popularity,
        recommendation_score=recommendation_score(score, popularity),
        episodes=media.get("episodes"),
        year=(media.get("startDate") or {}).get("year"),
        anilist_id=media["id"],
        mal_id=str(mal_id) if mal_id else "",
        anidb_id=str(anidb_id) if anidb_id else "",
        image=(media.get("coverImage") or {}).get("large", ""),
        owned=owned,
        is_franchise_root=not prequel_ids,
        sequel_of_owned=bool(owned_ids and (season_prequel_ids & owned_ids)),
        format=media.get("format") or "",
        status=media.get("status") or "",
        genres=media.get("genres") or [],
        tvdb_id=str(tvdb_id) if tvdb_id else "",
        tvdb_season=tvdb_season,
        sonarr_status=sonarr_status(
            tvdb_id, tvdb_season, sonarr_index, sonarr_available
        ),
    )


def compare_collections(anilist, mappings, mal_ids, anidb_ids, settings,
                        sonarr_index=None, sonarr_available=False,
                        owned_ids=None):
    """Match the AniList list against the Shoko library, and against Sonarr."""
    log.info("Comparing %s AniList entries against the Shoko library", len(anilist))

    by_mal = {}
    unmapped = 0

    for media in anilist:
        mapping = mappings.get(int(media["id"]))
        if not mapping or not mapping.get("mal_id"):
            unmapped += 1
            continue

        if (media.get("popularity") or 0) < settings.min_popularity:
            continue

        entry = _build_entry(media, mapping, mal_ids, anidb_ids,
                             sonarr_index, sonarr_available,
                             owned_ids=owned_ids)

        # Several AniList entries can map to one MAL ID; keep the best-ranked.
        existing = by_mal.get(entry.mal_id)
        if existing is None or entry.rank < existing.rank:
            by_mal[entry.mal_id] = entry

    results = sorted(by_mal.values(), key=lambda e: e.rank)
    log.info("Tracked entries: %s (%s AniList entries had no usable mapping)",
             len(results), unmapped)
    return results


def build_season_entries(media_list, mappings, mal_ids, anidb_ids,
                         sonarr_index=None, sonarr_available=False, limit=20,
                         owned_ids=None):
    """Rank one season's media, keeping entries the mapping doesn't know yet.

    Deliberately skips the popularity floor and the mapping requirement that
    compare_collections applies: a show airing next season is exactly the one
    Kometa hasn't mapped and nobody has rated yet, and dropping those would
    leave the page empty. Such entries simply can't be matched, and say so.
    """
    entries = []
    seen = set()

    for media in media_list:
        anilist_id = _as_int(media.get("id"))
        if anilist_id is None or anilist_id in seen:
            continue
        seen.add(anilist_id)

        entries.append(_build_entry(
            media, mappings.get(anilist_id), mal_ids, anidb_ids,
            sonarr_index, sonarr_available, rank=len(entries) + 1,
            owned_ids=owned_ids,
        ))

        if len(entries) >= limit:
            break

    return entries


def compare_sonarr(sonarr_series, tvdb_ids, shoko_episodes=None, tolerance=1):
    """Work out which Sonarr series already exist in Shoko, matched on TVDB.

    A series counts as `partial` when Shoko has it but is short of Sonarr's
    file count - a half-finished move, which reads as done on a bare
    migrated/not-migrated split.

    The counts are close but not strictly comparable: Shoko's local episode
    count generally leaves specials out, while Sonarr's includes season 0 when
    it is monitored. Hence the tolerance, and hence showing both raw numbers
    rather than a bare "incomplete" badge - the gap is a prompt to look, not a
    verdict.
    """
    results = []
    shoko_episodes = shoko_episodes or {}

    for series in sonarr_series:
        stats = series.get("statistics") or {}
        tvdb_id = series.get("tvdbId")

        migrated = bool(tvdb_id and str(tvdb_id) in tvdb_ids)
        files = stats.get("episodeFileCount", 0) or 0
        in_shoko = shoko_episodes.get(str(tvdb_id), 0) if tvdb_id else 0

        results.append(SonarrEntry(
            title=series.get("title", "Unknown"),
            tvdb_id=tvdb_id,
            status=series.get("status", ""),
            episode_file_count=files,
            episode_count=stats.get("episodeCount", 0) or 0,
            size_gb=round((stats.get("sizeOnDisk", 0) or 0) / GB, 2),
            migrated=migrated,
            shoko_episodes=in_shoko,
            # Zero on Shoko's side isn't a partial move, it's just not migrated
            # - or a version whose episode counts don't read at all.
            partial=bool(migrated and in_shoko and files - in_shoko > tolerance),
        ))

    log.info("Sonarr comparison: %s series, %s already in Shoko (%s partial)",
             len(results), sum(1 for r in results if r.migrated),
             sum(1 for r in results if r.partial))
    return results
