"""Aggregate statistics, tier breakdowns and run-over-run diffing."""

from core.models import Diff


def _avg(values):
    values = [v for v in values if v]
    return round(sum(values) / len(values), 2) if values else 0


def build_stats(results):
    owned = [r for r in results if r.owned]
    missing = [r for r in results if not r.owned]
    total = len(results)

    roots_missing = [r for r in missing if r.is_franchise_root]

    return {
        "total": total,
        "owned": len(owned),
        "missing": len(missing),
        "completion": round(len(owned) / total * 100, 2) if total else 0,
        "avg_owned_score": _avg([r.score for r in owned]),
        "avg_missing_score": _avg([r.score for r in missing]),
        "missing_roots": len(roots_missing),
        "owned_episodes": sum(r.episodes or 0 for r in owned),
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


def build_decade_breakdown(results):
    """Ownership grouped by decade - drives the secondary dashboard chart."""
    buckets = {}

    for entry in results:
        if not entry.year:
            continue
        decade = (int(entry.year) // 10) * 10
        bucket = buckets.setdefault(decade, {"decade": decade, "owned": 0, "total": 0})
        bucket["total"] += 1
        if entry.owned:
            bucket["owned"] += 1

    output = []
    for bucket in sorted(buckets.values(), key=lambda b: b["decade"]):
        bucket["completion"] = round(bucket["owned"] / bucket["total"] * 100, 2)
        bucket["label"] = f"{bucket['decade']}s"
        output.append(bucket)

    return output


def build_genre_breakdown(results, limit=10):
    """Most common genres among tracked series, with ownership rates."""
    buckets = {}

    for entry in results:
        for genre in entry.genres or []:
            bucket = buckets.setdefault(genre, {"genre": genre, "owned": 0, "total": 0})
            bucket["total"] += 1
            if entry.owned:
                bucket["owned"] += 1

    ranked = sorted(buckets.values(), key=lambda b: b["total"], reverse=True)[:limit]
    for bucket in ranked:
        bucket["completion"] = round(bucket["owned"] / bucket["total"] * 100, 2)

    return ranked


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
        "migrated_size_gb": round(sum(r.size_gb for r in migrated), 2),
    }


def build_library_totals(shoko_series, sonarr_series, shoko_episodes=0,
                         episodes_look_wrong=False):
    return {
        "shoko_shows": len(shoko_series),
        "shoko_episodes": shoko_episodes,
        "shoko_episodes_suspect": episodes_look_wrong,
        "sonarr_shows": len(sonarr_series),
        "sonarr_episodes": sum(
            (s.get("statistics") or {}).get("episodeFileCount", 0) or 0
            for s in sonarr_series
        ),
    }


def build_diff(results, previous_entries):
    """Compare against the previous run's stored entries."""
    diff = Diff()

    if not previous_entries:
        return diff

    diff.has_previous = True
    previous_lookup = {str(p.get("mal_id")): p for p in previous_entries}

    for result in results:
        prev = previous_lookup.get(result.mal_id)

        if prev is None:
            diff.newly_tracked.append(_summarise(result))
        elif result.owned and not prev.get("owned"):
            diff.newly_owned.append(_summarise(result))
        elif not result.owned and prev.get("owned"):
            diff.newly_missing.append(_summarise(result))

    return diff


def _summarise(entry):
    return {
        "title": entry.title,
        "rank": entry.rank,
        "anilist_id": entry.anilist_id,
        "mal_id": entry.mal_id,
        "image": entry.image,
    }
