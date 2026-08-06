"""Stats, tiers and run-over-run diffing."""

from core import stats as stats_mod
from core.models import (
    SONARR_MISSING,
    SONARR_OWNED,
    SONARR_UNKNOWN,
    SONARR_UNMAPPED,
    Entry,
    ShokoEntry,
    SonarrEntry,
)


def entry(rank, owned, score=80, year=2015, root=True, genres=None, mal=None,
          sonarr=SONARR_UNKNOWN):
    return Entry(
        rank=rank,
        title=f"Show {rank}",
        score=score,
        popularity=100000,
        recommendation_score=90.0,
        episodes=12,
        year=year,
        anilist_id=rank,
        mal_id=mal or str(rank),
        anidb_id="",
        image="",
        owned=owned,
        is_franchise_root=root,
        genres=genres or [],
        sonarr_status=sonarr,
    )


def test_build_stats():
    stats = stats_mod.build_stats([
        entry(1, True, score=90), entry(2, False, score=70), entry(3, False, score=80),
    ])
    assert stats["total"] == 3
    assert stats["owned"] == 1
    assert stats["missing"] == 2
    assert stats["completion"] == 33.33
    assert stats["avg_owned_score"] == 90
    assert stats["avg_missing_score"] == 75


def test_build_stats_on_an_empty_list_does_not_divide_by_zero():
    stats = stats_mod.build_stats([])
    assert stats["completion"] == 0
    assert stats["total"] == 0


def test_missing_roots_counts_only_unowned_roots():
    stats = stats_mod.build_stats([
        entry(1, False, root=True), entry(2, False, root=False), entry(3, True, root=True),
    ])
    assert stats["missing_roots"] == 1


def test_build_tiers_is_cumulative():
    entries = [entry(rank, owned=rank <= 50) for rank in (10, 50, 150, 400)]
    tiers = stats_mod.build_tiers(entries, [100, 250, 500])

    by_tier = {t["tier"]: t for t in tiers}
    assert by_tier[100]["owned"] == 2 and by_tier[100]["total"] == 2
    assert by_tier[250]["total"] == 3
    assert by_tier[500]["total"] == 4


def test_build_tiers_skips_empty_tiers():
    tiers = stats_mod.build_tiers([entry(900, True)], [100, 1000])
    assert [t["tier"] for t in tiers] == [1000]


def test_decade_breakdown():
    rows = stats_mod.build_decade_breakdown([
        entry(1, True, year=2015), entry(2, False, year=2019), entry(3, True, year=2005),
    ])
    by_decade = {r["decade"]: r for r in rows}
    assert by_decade[2010]["total"] == 2
    assert by_decade[2010]["owned"] == 1
    assert by_decade[2010]["label"] == "2010s"
    assert by_decade[2000]["completion"] == 100.0


def test_decade_breakdown_ignores_entries_without_a_year():
    rows = stats_mod.build_decade_breakdown([entry(1, True, year=None)])
    assert rows == []


def test_genre_breakdown_ranks_by_frequency():
    rows = stats_mod.build_genre_breakdown([
        entry(1, True, genres=["Action", "Drama"]),
        entry(2, False, genres=["Action"]),
    ])
    assert rows[0]["genre"] == "Action"
    assert rows[0]["total"] == 2
    assert rows[0]["completion"] == 50.0


def test_diff_detects_newly_owned_and_tracked():
    previous = [
        {"mal_id": "1", "owned": False},
        {"mal_id": "2", "owned": True},
    ]
    current = [entry(1, True, mal="1"), entry(2, True, mal="2"), entry(3, False, mal="3")]

    diff = stats_mod.build_diff(current, previous)

    assert diff.has_previous is True
    assert [d["mal_id"] for d in diff.newly_owned] == ["1"]
    assert [d["mal_id"] for d in diff.newly_tracked] == ["3"]
    assert diff.newly_missing == []


def test_diff_detects_something_that_stopped_matching():
    diff = stats_mod.build_diff(
        [entry(1, False, mal="1")], [{"mal_id": "1", "owned": True}]
    )
    assert [d["mal_id"] for d in diff.newly_missing] == ["1"]


def test_diff_without_previous_data_is_empty():
    diff = stats_mod.build_diff([entry(1, True)], None)
    assert diff.has_previous is False
    assert diff.newly_owned == []


def test_migration_stats():
    rows = [
        SonarrEntry("A", 1, "continuing", 10, 12, 5.0, True),
        SonarrEntry("B", 2, "ended", 20, 20, 7.5, False),
    ]
    migration = stats_mod.build_migration_stats(rows)

    assert migration["total"] == 2
    assert migration["migrated"] == 1
    assert migration["completion"] == 50.0
    assert migration["remaining_size_gb"] == 7.5
    assert migration["migrated_size_gb"] == 5.0


def test_migration_stats_with_no_sonarr_data():
    migration = stats_mod.build_migration_stats([])
    assert migration["total"] == 0
    assert migration["completion"] == 0


def test_migration_stats_without_shoko_rows_keeps_the_sonarr_side_shape():
    """Every key here has a reader - the page or Storage.finish_run."""
    migration = stats_mod.build_migration_stats([
        SonarrEntry("A", 1, "continuing", 10, 12, 5.0, True),
    ])
    assert set(migration) == {
        "total", "migrated", "remaining", "completion",
        "remaining_size_gb", "migrated_size_gb",
        "partial", "partial_missing_episodes", "unmappable",
    }


def test_unmappable_series_still_count_as_remaining():
    """Narrowing `remaining` would put a step in the run-history trend."""
    rows = [
        SonarrEntry("A", 1, "ended", 10, 12, 5.0, False),
        SonarrEntry("B", 2, "ended", 20, 20, 7.5, False, unmappable=True),
    ]
    migration = stats_mod.build_migration_stats(rows)

    assert migration["remaining"] == 2
    assert migration["remaining_size_gb"] == 12.5
    assert migration["unmappable"] == 1


def test_migration_stats_counts_both_sides():
    rows = [
        SonarrEntry("A", 1, "continuing", 24, 24, 5.0, True,
                    shoko_episodes=12, partial=True),
        SonarrEntry("B", 2, "ended", 20, 20, 7.5, False),
    ]
    shoko = [
        ShokoEntry("In both", "1", ["1"], "1", 12, SONARR_OWNED),
        ShokoEntry("Only here", "2", ["2"], "2", 8, SONARR_MISSING),
        ShokoEntry("A movie", "3", [], "", 1, SONARR_UNMAPPED),
    ]
    migration = stats_mod.build_migration_stats(rows, shoko)

    assert migration["partial"] == 1
    assert migration["partial_missing_episodes"] == 12
    assert migration["shoko_only_episodes"] == 8
    assert migration["shoko_unmapped"] == 1


# ==========================
# Shoko vs Sonarr
# ==========================

def test_comparison_splits_the_four_quadrants():
    comparison = stats_mod.build_comparison([
        entry(1, True, sonarr="owned"),
        entry(2, True, sonarr="missing"),
        entry(3, False, sonarr="owned"),
        entry(4, False, sonarr="missing"),
    ], sonarr_available=True)

    assert comparison["both"] == 1
    assert comparison["shoko_only"] == 1
    assert comparison["sonarr_only"] == 1
    assert comparison["neither"] == 1
    assert comparison["comparable"] == 4
    assert comparison["in_sonarr"] == 2


def test_comparison_counts_unmapped_entries_separately():
    """No TVDB ID is "can't tell", not "not in Sonarr"."""
    comparison = stats_mod.build_comparison([
        entry(1, False, sonarr="unmapped"),
        entry(2, False, sonarr="missing"),
    ], sonarr_available=True)

    assert comparison["unmapped"] == 1
    assert comparison["neither"] == 1
    assert comparison["comparable"] == 1


def test_a_series_monitored_but_empty_does_not_count_as_in_sonarr():
    comparison = stats_mod.build_comparison(
        [entry(1, False, sonarr="wanted")], sonarr_available=True
    )
    assert comparison["sonarr_only"] == 0
    assert comparison["neither"] == 1


def test_no_comparison_without_sonarr():
    assert stats_mod.build_comparison([entry(1, True)], sonarr_available=False) is None


def test_library_totals():
    totals = stats_mod.build_library_totals(
        shoko_series=[{}, {}],
        sonarr_series=[{"statistics": {"episodeFileCount": 10}},
                       {"statistics": {"episodeFileCount": 5}}],
        shoko_episodes=42,
    )
    assert totals["shoko_shows"] == 2
    assert totals["shoko_episodes"] == 42
    assert totals["sonarr_shows"] == 2
    assert totals["sonarr_episodes"] == 15
