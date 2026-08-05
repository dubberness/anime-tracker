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


def seed_blocks(seasons):
    """The season blocks worth seeding from: the current one, then the next.

    The upcoming season is included so a sequel is on autobrr's list before it
    starts airing rather than after the first episode has already gone by.

    Both callers - the run and the settings page's preview of what the next run
    would add - go through here. If they picked blocks separately they could
    drift, and an untracked show would come back on the next run because one
    side didn't consider it auto-seeded.
    """
    blocks = [b for b in (seasons or []) if b.get("is_current")]
    blocks += [b for b in (seasons or []) if b.get("is_upcoming")]
    return blocks


def auto_seed_candidates(seasons, excluded_ids=(), limit=10):
    """What autobrr should be told to grab, across this season and the next.

    Per season, in order: the top `limit` most popular that Shoko is missing,
    then any sequel of something Shoko already has that didn't make the cut.

    A sequel inside the top `limit` is simply one of them and uses a slot like
    anything else - no reordering. The second pass only rescues the ones ranked
    too low to be picked up, which is exactly where the next season of a show
    with a small audience tends to sit, and is the case popularity alone would
    always miss.

    The limit is counted per season rather than shared, so the current season
    can't use up the upcoming one's slots.

    Ranked off the popularity list rather than the page's active sort, so what
    gets tracked doesn't depend on which toggle happened to be clicked last.
    Anything explicitly untracked stays out, otherwise the next run would
    silently undo the decision.
    """
    # Zero is the off switch, and it turns the whole thing off - rescuing
    # sequels anyway would make "0" mean "some".
    if limit <= 0:
        return []

    excluded = {int(value) for value in excluded_ids}

    def eligible(block):
        entries = (block.get("sorts") or {}).get("popularity") or []
        return [
            entry for entry in entries
            if not entry.get("owned") and entry.get("anilist_id") not in excluded
        ]

    candidates = []
    seen = set()

    def take(entry):
        anilist_id = entry.get("anilist_id")
        if anilist_id in seen:
            return
        seen.add(anilist_id)
        candidates.append(entry)

    for block in seed_blocks(seasons):
        entries = eligible(block)

        for entry in entries[:limit]:
            take(entry)

        for entry in entries[limit:]:
            if entry.get("sequel_of_owned"):
                take(entry)

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
