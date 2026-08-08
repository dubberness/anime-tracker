"""Deciding what autobrr should be told to grab.

Pure functions over already-fetched data, like the rest of core/. autobrr
itself owns the filter - indexers, quality, release groups, download client.
All this module decides is the set of *titles* that filter should match.
"""

from datetime import datetime, timedelta

from core import compare
from logging_setup import get_logger

log = get_logger(__name__)

AUTO = "auto"
MANUAL = "manual"

RELEASING = "RELEASING"
FINISHED = "FINISHED"
NOT_YET_RELEASED = "NOT_YET_RELEASED"
CANCELLED = "CANCELLED"
HIATUS = "HIATUS"

# States where more episodes are still to come. An unrecognised or missing
# status is deliberately not in here: results from before this field was
# recorded should behave as they used to.
STILL_COMING = (RELEASING, NOT_YET_RELEASED, HIATUS)

# How long a finished-but-incomplete show stays on the list. See the comment on
# AutobrrSettings.finished_grace_days for why this is not zero.
FINISHED_GRACE_DAYS = 14

# A row that has never been matched to any AniList media this long after being
# added is not going to be. Only ever applied when AniList actually answered.
UNKNOWN_STALE_DAYS = 120


def is_done(entry):
    """Whether there is genuinely nothing left for autobrr to grab.

    Not the same thing as compare.is_complete, and conflating the two is what
    made a caught-up airing show untrackable: "Shoko has every episode that has
    aired" is true of a weekly show the morning after each episode lands, but
    next week's is exactly what autobrr is for.

    Done means complete *and* nothing more coming. A show still releasing is
    never done, however up to date the library is.
    """
    return compare.is_complete(entry) and entry.get("status") not in STILL_COMING


def is_now_owned(row, mal_ids, anidb_ids):
    """Whether Shoko has picked up a tracked show since it was added.

    The same rule the rest of the app matches on, but against the IDs stored
    on the row rather than a live mapping lookup - a tracked show can be too
    new or too obscure to appear in the tracked list at all.
    """
    return compare.matches_shoko(
        row.get("mal_id"), row.get("anidb_id"), mal_ids, anidb_ids,
    )


def _parse_time(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _is_stale(row, now, days):
    added = _parse_time(row.get("added_at"))
    return added is not None and now - added > timedelta(days=days)


def should_stay_tracked(row, status, owned, complete=None, now=None,
                        grace_days=FINISHED_GRACE_DAYS,
                        unknown_stale_days=UNKNOWN_STALE_DAYS,
                        anilist_answered=True):
    """Whether one tracked row still belongs on the autobrr list.

    `status` is the AniList status observed this run, or None when no status
    could be established for this row.

    `complete` tri-states: True, False, or None to fall back to plain `owned`,
    which is the pre-4.3 rule and the right answer when episode counts are
    unavailable.

    Returns (keep, reason). The reason goes straight into the log, so an
    untrack is never mysterious after the fact.
    """
    now = now or datetime.now()
    complete = owned if complete is None else complete

    # An AniList outage must never mass-untrack. Doing so would hand autobrr an
    # empty list and silently stop every grab, which is far worse than carrying
    # a few stale titles for a night.
    if status is None:
        if (anilist_answered
                and not row.get("status")
                and _is_stale(row, now, unknown_stale_days)):
            return False, "never identified"
        return True, "status unknown"

    if status == RELEASING:
        # Airing beats owned. Shoko registers a series on its first episode, so
        # treating ownership as "done" here is what stopped autobrr grabbing
        # anything past episode one.
        return True, "airing"

    if status == NOT_YET_RELEASED:
        return True, "not aired yet"

    if status == HIATUS:
        return True, "on hiatus"

    if status == CANCELLED:
        return False, "cancelled"

    if status == FINISHED:
        if complete:
            return False, "complete"

        changed = _parse_time(row.get("status_at"))
        if changed is None:
            # First run that has seen the finish; the caller stamps status_at
            # just before this, so a missing stamp means keep and re-check.
            return True, "finished, in grace"

        if now - changed < timedelta(days=grace_days):
            return True, "finished, in grace"

        return False, "finished, not coming"

    # An unrecognised status is not a reason to drop anything - fail open.
    return True, "status unknown"


def _local_count(row, local_by_mal, local_by_anidb):
    """Episodes Shoko holds for a tracked row - max, not sum, across ID kinds."""
    mal_id = str(row.get("mal_id") or "")
    anidb_id = str(row.get("anidb_id") or "")

    return max(
        (local_by_mal or {}).get(mal_id, 0) if mal_id else 0,
        (local_by_anidb or {}).get(anidb_id, 0) if anidb_id else 0,
    )


def prune_plan(rows, statuses, mal_ids, anidb_ids,
               local_by_mal=None, local_by_anidb=None, now=None,
               grace_days=FINISHED_GRACE_DAYS):
    """Which tracked rows to drop this run, and why.

    `statuses` maps anilist_id -> what AniList said about it this run. Pass
    None when AniList could not be reached at all: every row then survives,
    and the never-identified age-out is suppressed too.

    Returns [(row, reason), ...] - only the drops, so the caller loops over
    exactly what it is about to change.
    """
    answered = statuses is not None
    statuses = statuses or {}
    drops = []

    for row in rows:
        media = statuses.get(row["anilist_id"])
        owned = is_now_owned(row, mal_ids, anidb_ids)

        complete = None
        if media is not None:
            complete = compare.is_complete({
                "owned": owned,
                "episodes_aired": media.get("episodes_aired"),
                "episodes_local": _local_count(
                    row, local_by_mal, local_by_anidb
                ),
            })

        keep, reason = should_stay_tracked(
            row,
            media.get("status") if media else None,
            owned,
            complete=complete,
            now=now,
            grace_days=grace_days,
            anilist_answered=answered,
        )

        if not keep:
            drops.append((row, reason))

    return drops


def upcoming_blocks(seasons):
    """The season blocks worth seeding from alongside the airing list.

    Only the upcoming season. The current one used to be here too, but the
    airing list covers it and more: AniList tags media with the season it
    *started* in, so a two-cour show drops off the current chart halfway
    through its run while still going out weekly. Seeding from that chart
    could never see it.

    The upcoming season is the one case the airing list structurally cannot
    cover - nothing there is RELEASING yet - and it earns its place by getting
    a sequel onto autobrr's list before episode one rather than after.

    Both callers - the run and the settings page's preview of what the next
    run would add - go through here. If they picked blocks separately they
    could drift, and an untracked show would come back on the next run because
    one side didn't consider it auto-seeded.
    """
    return [b for b in (seasons or []) if b.get("is_upcoming")]


def _seed_source(entries, excluded, airing_only):
    """One ranked list of eligible candidates, most popular first."""
    eligible = []

    for entry in entries or []:
        if entry.get("anilist_id") in excluded:
            continue
        # Only a show with nothing left to come is skipped. Owning an earlier
        # cour is not a reason - a split-cour sequel is a separate AniList
        # entry that the mapping file points at part one's MAL ID - and
        # neither is being caught up on a show that is still airing, which
        # would drop it from the list the week before its next episode.
        if is_done(entry):
            continue
        # Auto-tracking One Piece means autobrr chasing eleven hundred
        # episodes. Still trackable by hand, just never automatically.
        if entry.get("is_long_runner"):
            continue
        # Guards against re-adding something the prune just aged out. Only
        # applied to the airing list; upcoming-season shows are by definition
        # NOT_YET_RELEASED.
        if airing_only and entry.get("status") != RELEASING:
            continue

        eligible.append(entry)

    return eligible


def auto_seed_candidates(airing, seasons=None, excluded_ids=(), limit=10):
    """What autobrr should be told to grab, across what's airing and what's next.

    Per source, in order: the top `limit` most popular that Shoko doesn't have
    in full, then any sequel of something Shoko already has that didn't make
    the cut.

    A sequel inside the top `limit` is simply one of them and uses a slot like
    anything else - no reordering. The second pass only rescues the ones ranked
    too low to be picked up, which is exactly where the next season of a show
    with a small audience tends to sit, and is the case popularity alone would
    always miss.

    The limit is counted per source rather than shared, so what's airing can't
    use up the upcoming season's slots.

    `airing` is None when AniList couldn't be reached. The upcoming season
    block is unaffected by that and still seeds; what deliberately does *not*
    happen is falling back to the current season's chart, which would quietly
    reintroduce the carryover blind spot this exists to fix.
    """
    # Zero is the off switch, and it turns the whole thing off - rescuing
    # sequels anyway would make "0" mean "some".
    if limit <= 0:
        return []

    excluded = {int(value) for value in excluded_ids}

    sources = [_seed_source(airing, excluded, airing_only=True)]
    for block in upcoming_blocks(seasons):
        entries = (block.get("sorts") or {}).get("popularity") or []
        sources.append(_seed_source(entries, excluded, airing_only=False))

    candidates = []
    seen = set()

    def take(entry):
        anilist_id = entry.get("anilist_id")
        if anilist_id in seen:
            return
        seen.add(anilist_id)
        candidates.append(entry)

    for entries in sources:
        for entry in entries[:limit]:
            take(entry)

        for entry in entries[limit:]:
            if entry.get("sequel_of_owned"):
                take(entry)

    return candidates


def airing_entries(results):
    """The airing entries out of a results payload, or [] on older ones."""
    return ((results or {}).get("airing") or {}).get("entries") or []


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
