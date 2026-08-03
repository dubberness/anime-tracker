"""Deciding what autobrr should be told to grab.

Pure functions over already-fetched data, like the rest of core/. autobrr
itself owns the filter - indexers, quality, release groups, download client.
All this module decides is the set of *titles* that filter should match.
"""

from logging_setup import get_logger

log = get_logger(__name__)

AUTO = "auto"
MANUAL = "manual"


def is_now_owned(row, mal_ids, anidb_ids):
    """Whether Shoko has picked up a tracked show since it was added.

    Same rule as compare.compare_collections, but against the IDs stored on
    the row rather than a live mapping lookup - a tracked show can be too new
    or too obscure to appear in the tracked list at all.
    """
    mal_id = row.get("mal_id")
    anidb_id = row.get("anidb_id")

    return bool(
        (mal_id and str(mal_id) in mal_ids)
        or (anidb_id and str(anidb_id) in anidb_ids)
    )


def auto_seed_candidates(season_block, excluded_ids=(), limit=10):
    """The current season's most popular shows that Shoko is missing.

    Ranked by popularity rather than the page's active sort, so what gets
    tracked doesn't depend on which toggle happened to be clicked last.
    Anything explicitly untracked stays out, otherwise the next run would
    silently undo the decision.
    """
    if not season_block or limit <= 0:
        return []

    excluded = {int(value) for value in excluded_ids}
    entries = (season_block.get("sorts") or {}).get("popularity") or []

    candidates = []
    for entry in entries:
        if entry.get("owned"):
            continue
        if entry.get("anilist_id") in excluded:
            continue

        candidates.append(entry)
        if len(candidates) >= limit:
            break

    return candidates


def build_list_text(rows):
    """The plaintext body autobrr polls: one title per line.

    Both spellings go in where they differ, since a release group will use one
    or the other. An empty list is an empty body, not an error - "nothing
    tracked yet" is a normal state and a 404 would just look broken.
    """
    lines = []

    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            continue

        lines.append(title)

        alt = (row.get("title_alt") or "").strip()
        if alt and alt != title:
            lines.append(alt)

    return "\n".join(lines)
