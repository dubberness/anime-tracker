"""Ownership matching - the rules that decide what shows as owned."""

import config as config_mod
from core import compare


def media(anilist_id, rank, popularity=100000, score=80, prequel=False,
          next_episode=None, **kwargs):
    entry = {
        "id": anilist_id,
        "rank": rank,
        "title": {"english": f"Show {anilist_id}", "romaji": f"Shou {anilist_id}"},
        "averageScore": score,
        "popularity": popularity,
        "episodes": 12,
        "startDate": {"year": 2015},
        "coverImage": {"large": "http://img/x.jpg"},
        "relations": {"edges": []},
    }
    if prequel:
        entry["relations"]["edges"].append(prequel_edge(1))
    if next_episode is not None:
        entry["nextAiringEpisode"] = {"episode": next_episode, "airingAt": 1700000000}
    entry.update(kwargs)
    return entry


# ==========================
# ID extraction
# ==========================

def test_extracts_ids_from_the_ids_object():
    mal, anidb, tvdb = compare.extract_shoko_ids([
        {"IDs": {"AniDB": 123, "TvDB": [456], "MAL": [789]}},
    ])
    assert anidb == {"123"}
    assert tvdb == {"456"}
    assert mal == {"789"}


def test_extracts_ids_from_the_links_fallback():
    mal, anidb, tvdb = compare.extract_shoko_ids([
        {"Links": [
            {"Name": "AniDB", "URL": "https://anidb.net/anime/999"},
            {"Name": "MyAnimeList", "URL": "https://myanimelist.net/anime/555"},
            {"Name": "TheTVDB", "URL": "https://thetvdb.com/series/777"},
        ]},
    ])
    assert anidb == {"999"}
    assert mal == {"555"}
    assert tvdb == {"777"}


def test_id_extraction_survives_empty_and_missing_shapes():
    mal, anidb, tvdb = compare.extract_shoko_ids([
        {}, {"IDs": {}}, {"IDs": {"AniDB": None}}, {"Links": []},
    ])
    assert (mal, anidb, tvdb) == (set(), set(), set())


def test_tvdb_is_cross_checked_against_the_mapping_file():
    """Shoko's own TVDB field can be wrong - e.g. Naruto Shippuuden (AniDB
    4880) coming back with the original Naruto's TVDB ID instead of its own.
    The mapping file's TVDB ID for that AniDB ID is added on top rather than
    trusted in place of Shoko's, so either source finding it is enough.
    """
    mappings = {
        1735: {"anidb_id": "4880", "tvdb_id": 79824, "mal_id": 1735},
    }
    mal, anidb, tvdb = compare.extract_shoko_ids(
        [{"IDs": {"AniDB": 4880, "TvDB": [78857]}}], mappings,
    )
    assert anidb == {"4880"}
    assert tvdb == {"78857", "79824"}


def test_tvdb_mapping_lookup_also_covers_the_links_fallback():
    mappings = {1735: {"anidb_id": "4880", "tvdb_id": 79824}}
    mal, anidb, tvdb = compare.extract_shoko_ids(
        [{"Links": [{"Name": "AniDB", "URL": "https://anidb.net/anime/4880"}]}],
        mappings,
    )
    assert tvdb == {"79824"}


def test_tvdb_mapping_lookup_is_a_noop_without_a_mapping_file():
    mal, anidb, tvdb = compare.extract_shoko_ids(
        [{"IDs": {"AniDB": 4880, "TvDB": [78857]}}],
    )
    assert tvdb == {"78857"}


# ==========================
# Episode counting
# ==========================

def test_counts_episodes_across_known_shapes():
    total, suspect = compare.count_shoko_episodes([
        {"Sizes": {"Local": {"Episodes": 12}}},
        {"Sizes": {"Local": {"Total": 5}}},
        {"EpisodeCount": 3},
    ])
    assert total == 20
    assert suspect is False


def test_flags_a_zero_count_on_a_non_empty_library():
    total, suspect = compare.count_shoko_episodes([{"Name": "x"}])
    assert total == 0
    assert suspect is True


def test_empty_library_is_not_suspect():
    total, suspect = compare.count_shoko_episodes([])
    assert (total, suspect) == (0, False)


def test_per_series_counts_cover_the_dict_shaped_fallback():
    assert compare.shoko_episode_count({"Sizes": {"Local": {"Episodes": 12}}}) == 12
    assert compare.shoko_episode_count({"Sizes": {"Local": {"Total": 5}}}) == 5
    assert compare.shoko_episode_count({"EpisodeCount": 3}) == 3
    assert compare.shoko_episode_count(
        {"Sizes": {"Local": {"Episodes": {"Episodes": 7}}}}
    ) == 7
    assert compare.shoko_episode_count({"Sizes": {"Local": {"Episodes": "nope"}}}) == 0


# ==========================
# The Shoko side of the migration
# ==========================

SHOKO_LIBRARY = [
    {"Name": "Alpha", "IDs": {"AniDB": 1, "TvDB": [100]},
     "Sizes": {"Local": {"Episodes": 12}}},
    {"Title": "Beta", "IDs": {"AniDB": 2},
     "Sizes": {"Local": {"Episodes": 5}}},
    {"IDs": {"AniDB": 3, "TvDB": [300]}, "Sizes": {"Local": {"Episodes": 1}}},
]


def test_the_index_returns_the_same_sets_as_the_id_extractor():
    """The refactor's safety net: one walk, two views, no disagreement."""
    entries, mal, anidb, tvdb = compare.build_shoko_index(SHOKO_LIBRARY)
    assert (mal, anidb, tvdb) == compare.extract_shoko_ids(SHOKO_LIBRARY)
    assert len(entries) == len(SHOKO_LIBRARY)


def test_shoko_rows_take_a_title_from_either_field():
    entries, _, _, _ = compare.build_shoko_index(SHOKO_LIBRARY)
    assert [e.title for e in entries] == ["Alpha", "Beta", "Unknown"]


def test_a_shoko_series_with_no_tvdb_id_is_unmapped_not_missing():
    """Most of a library is movies and OVAs. Calling those "only in Shoko"
    would bury the handful of rows that actually mean something."""
    entries, _, _, _ = compare.build_shoko_index(SHOKO_LIBRARY)
    compare.annotate_shoko_sonarr(entries, {"100": {"episode_file_count": 12}})

    assert entries[1].sonarr_status == "unmapped"
    assert entries[0].sonarr_status == "owned"
    assert entries[2].sonarr_status == "missing"
    assert [e.title for e in compare.shoko_only(entries)] == ["Unknown"]


def test_a_shoko_series_matches_on_either_of_its_tvdb_ids():
    """The mapping-derived ID may be the one Sonarr knows it by, and it can
    sort after the stale one - so every candidate has to be tried."""
    mappings = {1735: {"anidb_id": "4880", "tvdb_id": 79824}}
    entries, _, _, _ = compare.build_shoko_index(
        [{"Name": "Shippuuden", "IDs": {"AniDB": 4880, "TvDB": [78857]}}], mappings,
    )
    compare.annotate_shoko_sonarr(entries, {"79824": {"episode_file_count": 500}})

    assert entries[0].sonarr_status == "owned"
    assert entries[0].tvdb_id == "79824"


def test_shoko_rows_read_as_unknown_when_sonarr_is_unavailable():
    entries, _, _, _ = compare.build_shoko_index(SHOKO_LIBRARY)
    compare.annotate_shoko_sonarr(entries, {}, sonarr_available=False)

    assert {e.sonarr_status for e in entries} == {"unknown"}
    assert compare.shoko_only(entries) == []


def test_a_series_in_sonarr_with_no_files_is_wanted_and_worth_listing():
    entries, _, _, _ = compare.build_shoko_index(SHOKO_LIBRARY)
    compare.annotate_shoko_sonarr(entries, {"100": {"episode_file_count": 0}})

    assert entries[0].sonarr_status == "wanted"
    assert entries[0] in compare.shoko_only(entries)


def test_episodes_are_summed_per_tvdb_id():
    """Sonarr is one row per TVDB series; Shoko is roughly one per season."""
    entries, _, _, _ = compare.build_shoko_index([
        {"Name": "S1", "IDs": {"AniDB": 1, "TvDB": [100]},
         "Sizes": {"Local": {"Episodes": 12}}},
        {"Name": "S2", "IDs": {"AniDB": 2, "TvDB": [100]},
         "Sizes": {"Local": {"Episodes": 13}}},
    ])
    assert compare.shoko_episodes_by_tvdb(entries) == {"100": 25}


# ==========================
# Partial migrations
# ==========================

def migration_row(tvdb_id, files, total=24):
    return {
        "title": f"Series {tvdb_id}",
        "tvdbId": tvdb_id,
        "status": "ended",
        "statistics": {
            "episodeFileCount": files,
            "episodeCount": total,
            "sizeOnDisk": 0,
        },
    }


def test_a_series_shoko_is_short_on_is_partial():
    results = compare.compare_sonarr(
        [migration_row(100, files=24)], {"100"}, {"100": 12},
    )
    assert results[0].migrated is True
    assert results[0].partial is True
    assert results[0].shoko_episodes == 12


def test_a_one_episode_gap_is_within_tolerance():
    """Shoko generally leaves specials out where Sonarr counts them."""
    results = compare.compare_sonarr(
        [migration_row(100, files=13)], {"100"}, {"100": 12},
    )
    assert results[0].partial is False


def test_a_series_not_in_shoko_at_all_is_not_partial():
    results = compare.compare_sonarr([migration_row(100, files=24)], set(), {})
    assert results[0].migrated is False
    assert results[0].partial is False


def test_zero_episodes_on_shokos_side_is_not_partial():
    """That reads as "not migrated", or as a version whose counts don't parse."""
    results = compare.compare_sonarr(
        [migration_row(100, files=24)], {"100"}, {"100": 0},
    )
    assert results[0].partial is False


def test_compare_sonarr_still_works_without_shoko_counts():
    results = compare.compare_sonarr([migration_row(100, files=24)], {"100"})
    assert results[0].migrated is True
    assert results[0].partial is False


# ==========================
# Series that can't be checked either way
# ==========================

def test_mapping_tvdb_ids_collects_every_reachable_tvdb_id():
    """Reachable means "the AniDB->TVDB index crosses it", the same route the
    matching takes - a row with no AniDB ID is no bridge at all."""
    mappings = {1: {"anidb_id": "11", "tvdb_id": 100},
                2: {"anidb_id": "22", "tvdb_id": "200"},
                3: {"anidb_id": "33", "mal_id": "9"},
                4: {"anidb_id": "44", "tvdb_id": None},
                5: {"tvdb_id": 500}}
    assert compare.mapping_tvdb_ids(mappings) == {"100", "200"}
    assert compare.mapping_tvdb_ids(None) == set()


def test_a_tvdb_id_the_mapping_never_heard_of_is_unmappable():
    """The Digimon Adventure 02 case: TheTVDB split the series, Sonarr picked
    up the new ID, the mapping still points at the old combined entry."""
    results = compare.compare_sonarr(
        [migration_row(459436, files=50)], set(), mapped_tvdb={"72241"},
    )
    assert results[0].migrated is False
    assert results[0].unmappable is True


def test_a_mapped_but_absent_series_is_missing_not_unmappable():
    """A real answer - the mapping knows it and Shoko doesn't have it."""
    results = compare.compare_sonarr(
        [migration_row(100, files=24)], set(), mapped_tvdb={"100"},
    )
    assert results[0].migrated is False
    assert results[0].unmappable is False


def test_a_sonarr_series_with_no_tvdb_id_at_all_is_unmappable():
    results = compare.compare_sonarr(
        [migration_row(None, files=24)], set(), mapped_tvdb={"100"},
    )
    assert results[0].unmappable is True


def test_a_matched_series_is_never_flagged_unmappable():
    """It's already answered, whatever the mapping file thinks."""
    results = compare.compare_sonarr(
        [migration_row(459436, files=50)], {"459436"}, mapped_tvdb={"72241"},
    )
    assert results[0].migrated is True
    assert results[0].unmappable is False


def test_nothing_is_unmappable_when_no_mapping_set_is_passed():
    """Callers predating the flag expect every row to be answerable."""
    results = compare.compare_sonarr([migration_row(459436, files=50)], set())
    assert results[0].unmappable is False


# ==========================
# New seasons of owned shows
# ==========================

def test_owned_anilist_ids_resolve_through_either_id():
    mappings = {10: {"mal_id": "1", "anidb_id": "2"},
                20: {"mal_id": "3", "anidb_id": "4"},
                30: {"mal_id": "5", "anidb_id": "6"}}

    assert compare.owned_anilist_ids(mappings, {"1"}, set()) == {10}
    assert compare.owned_anilist_ids(mappings, set(), {"4"}) == {20}
    assert compare.owned_anilist_ids(mappings, set(), set()) == set()


def prequel_edge(node_id, node_format=None):
    node = {"id": node_id, "type": "ANIME"}
    if node_format:
        node["format"] = node_format
    return {"relationType": "PREQUEL", "node": node}


def season_entry(edges, owned_ids=None):
    item = media(2, 1)
    item["relations"] = {"edges": edges}
    return compare.build_season_entries(
        [item], {}, set(), set(), owned_ids=owned_ids,
    )[0]


def test_a_new_season_of_an_owned_show_is_flagged():
    assert season_entry([prequel_edge(1, "TV")], owned_ids={1}).sequel_of_owned is True


def test_a_sequel_of_something_unowned_is_not_flagged():
    assert season_entry([prequel_edge(1, "TV")], owned_ids={99}).sequel_of_owned is False


def test_a_show_with_no_relations_is_not_flagged():
    assert season_entry([]).sequel_of_owned is False


def test_nothing_is_flagged_without_the_owned_set():
    """The default path - compare_collections callers that don't pass it."""
    assert season_entry([prequel_edge(1, "TV")]).sequel_of_owned is False


def test_a_special_prequel_does_not_make_it_a_new_season():
    """A recap special isn't the season before; the sequel just follows it."""
    assert season_entry([prequel_edge(1, "SPECIAL")], owned_ids={1}).sequel_of_owned is False


def test_a_prequel_with_no_format_still_counts():
    """Cached AniList responses predate the format field being requested."""
    assert season_entry([prequel_edge(1)], owned_ids={1}).sequel_of_owned is True


def test_a_special_prequel_still_means_it_is_not_a_franchise_root():
    """Root-ness asks whether anything came before, of any kind."""
    entry = season_entry([prequel_edge(1, "SPECIAL")])
    assert entry.is_franchise_root is False
    assert entry.sequel_of_owned is False


# ==========================
# Ownership
# ==========================

def _settings(min_popularity=0):
    settings = config_mod.AniListSettings()
    settings.min_popularity = min_popularity
    return settings


def test_matches_ownership_on_mal_id():
    results = compare.compare_collections(
        [media(1, 1)],
        {1: {"mal_id": "100", "anidb_id": "200"}},
        mal_ids={"100"}, anidb_ids=set(), settings=_settings(),
    )
    assert results[0].owned is True


def test_matches_ownership_on_anidb_id_alone():
    results = compare.compare_collections(
        [media(1, 1)],
        {1: {"mal_id": "100", "anidb_id": "200"}},
        mal_ids=set(), anidb_ids={"200"}, settings=_settings(),
    )
    assert results[0].owned is True


def test_unmatched_entry_is_missing():
    results = compare.compare_collections(
        [media(1, 1)],
        {1: {"mal_id": "100", "anidb_id": "200"}},
        mal_ids={"999"}, anidb_ids={"888"}, settings=_settings(),
    )
    assert results[0].owned is False


def test_entries_without_a_mapping_are_skipped():
    results = compare.compare_collections(
        [media(1, 1), media(2, 2)],
        {1: {"mal_id": "100"}},
        mal_ids=set(), anidb_ids=set(), settings=_settings(),
    )
    assert [r.anilist_id for r in results] == [1]


def test_popularity_threshold_filters_entries():
    results = compare.compare_collections(
        [media(1, 1, popularity=10), media(2, 2, popularity=90000)],
        {1: {"mal_id": "1"}, 2: {"mal_id": "2"}},
        mal_ids=set(), anidb_ids=set(), settings=_settings(min_popularity=50000),
    )
    assert [r.anilist_id for r in results] == [2]


def test_duplicate_mal_ids_keep_the_best_rank():
    results = compare.compare_collections(
        [media(1, 5), media(2, 2)],
        {1: {"mal_id": "same"}, 2: {"mal_id": "same"}},
        mal_ids=set(), anidb_ids=set(), settings=_settings(),
    )
    assert len(results) == 1
    assert results[0].rank == 2


def test_franchise_root_detection():
    results = compare.compare_collections(
        [media(1, 1), media(2, 2, prequel=True)],
        {1: {"mal_id": "1"}, 2: {"mal_id": "2"}},
        mal_ids=set(), anidb_ids=set(), settings=_settings(),
    )
    roots = {r.anilist_id: r.is_franchise_root for r in results}
    assert roots == {1: True, 2: False}


def test_results_are_sorted_by_rank():
    results = compare.compare_collections(
        [media(1, 9), media(2, 3), media(3, 6)],
        {1: {"mal_id": "1"}, 2: {"mal_id": "2"}, 3: {"mal_id": "3"}},
        mal_ids=set(), anidb_ids=set(), settings=_settings(),
    )
    assert [r.rank for r in results] == [3, 6, 9]


def test_title_prefers_english_then_romaji():
    entry = media(1, 1)
    entry["title"] = {"english": None, "romaji": "Romaji Name"}
    results = compare.compare_collections(
        [entry], {1: {"mal_id": "1"}},
        mal_ids=set(), anidb_ids=set(), settings=_settings(),
    )
    assert results[0].title == "Romaji Name"


def test_recommendation_score_blends_rating_and_reach():
    # Equal scores: the more popular series must rank higher.
    assert compare.recommendation_score(80, 1000000) > compare.recommendation_score(80, 1000)
    # Zero popularity must not blow up on log10.
    assert compare.recommendation_score(80, 0) > 0


# ==========================
# Sonarr
# ==========================

def test_sonarr_matches_on_tvdb_id():
    results = compare.compare_sonarr(
        [
            {"title": "A", "tvdbId": 111, "statistics": {"sizeOnDisk": 1073741824,
                                                         "episodeFileCount": 10,
                                                         "episodeCount": 12}},
            {"title": "B", "tvdbId": 222, "statistics": {}},
        ],
        tvdb_ids={"111"},
    )
    assert results[0].migrated is True
    assert results[0].size_gb == 1.0
    assert results[1].migrated is False


def test_sonarr_series_without_a_tvdb_id_is_not_migrated():
    results = compare.compare_sonarr([{"title": "A", "tvdbId": None}], tvdb_ids={"111"})
    assert results[0].migrated is False


# ==========================
# Sonarr status on tracked entries
# ==========================

def sonarr_series(tvdb_id, files=0, seasons=None):
    return {
        "title": f"Series {tvdb_id}",
        "tvdbId": tvdb_id,
        "statistics": {"episodeFileCount": files},
        "seasons": [
            {"seasonNumber": number, "statistics": {"episodeFileCount": count}}
            for number, count in (seasons or {}).items()
        ],
    }


def test_sonarr_index_keys_on_tvdb_id_and_keeps_season_counts():
    index = compare.build_sonarr_index([
        sonarr_series(111, files=24, seasons={1: 12, 2: 12}),
        sonarr_series(None),
    ])

    assert set(index) == {"111"}
    assert index["111"]["seasons"] == {1: 12, 2: 12}
    assert index["111"]["episode_file_count"] == 24


def test_sonarr_status_states():
    index = compare.build_sonarr_index([sonarr_series(111, files=12, seasons={1: 12})])

    assert compare.sonarr_status("111", 1, index) == "owned"
    assert compare.sonarr_status("222", 1, index) == "missing"
    assert compare.sonarr_status(None, None, index) == "unmapped"
    assert compare.sonarr_status("111", 1, index, available=False) == "unknown"


def test_a_season_with_no_files_is_wanted_not_owned():
    """Season 1 being on disk must not mark season 2 as owned."""
    index = compare.build_sonarr_index([
        sonarr_series(111, files=12, seasons={1: 12, 2: 0}),
    ])

    assert compare.sonarr_status("111", 1, index) == "owned"
    assert compare.sonarr_status("111", 2, index) == "wanted"


def test_unknown_season_falls_back_to_the_series_total():
    index = compare.build_sonarr_index([
        sonarr_series(111, files=12, seasons={1: 12}),
    ])

    assert compare.sonarr_status("111", -1, index) == "owned"
    assert compare.sonarr_status("111", None, index) == "owned"


def test_tracked_entries_carry_their_sonarr_status():
    index = compare.build_sonarr_index([sonarr_series(555, files=12, seasons={1: 12})])

    results = compare.compare_collections(
        [media(1, 1), media(2, 2)],
        {
            1: {"mal_id": "100", "tvdb_id": 555, "tvdb_season": 1},
            2: {"mal_id": "200", "tvdb_id": 999, "tvdb_season": 1},
        },
        mal_ids=set(), anidb_ids=set(), settings=_settings(),
        sonarr_index=index, sonarr_available=True,
    )

    assert results[0].sonarr_status == "owned"
    assert results[0].tvdb_id == "555"
    assert results[1].sonarr_status == "missing"


def test_sonarr_status_is_unknown_when_sonarr_was_not_consulted():
    results = compare.compare_collections(
        [media(1, 1)], {1: {"mal_id": "100", "tvdb_id": 555}},
        mal_ids=set(), anidb_ids=set(), settings=_settings(),
    )
    assert results[0].sonarr_status == "unknown"


# ==========================
# Season entries
# ==========================

def test_season_entries_are_ranked_by_list_position():
    entries = compare.build_season_entries(
        [media(7, 0), media(8, 0), media(9, 0)],
        {}, mal_ids=set(), anidb_ids=set(),
    )
    assert [e.rank for e in entries] == [1, 2, 3]
    assert [e.anilist_id for e in entries] == [7, 8, 9]


def test_season_entries_keep_unmapped_shows():
    """Next season's shows are exactly the ones Kometa hasn't mapped yet."""
    entries = compare.build_season_entries(
        [media(1, 0, popularity=5)], {}, mal_ids=set(), anidb_ids=set(),
    )
    assert len(entries) == 1
    assert entries[0].mal_id == ""
    assert entries[0].owned is False
    assert entries[0].sonarr_status == "unknown"


def test_season_entries_ignore_the_popularity_floor():
    entries = compare.build_season_entries(
        [media(1, 0, popularity=3)], {1: {"mal_id": "100"}},
        mal_ids={"100"}, anidb_ids=set(),
    )
    assert entries[0].owned is True


def test_season_entries_respect_the_limit():
    entries = compare.build_season_entries(
        [media(i, 0) for i in range(1, 30)], {},
        mal_ids=set(), anidb_ids=set(), limit=20,
    )
    assert len(entries) == 20


def test_season_entries_drop_duplicates():
    entries = compare.build_season_entries(
        [media(1, 0), media(1, 0), media(2, 0)], {},
        mal_ids=set(), anidb_ids=set(),
    )
    assert [e.anilist_id for e in entries] == [1, 2]


# ==========================
# Per-series episode counts
# ==========================

def test_per_series_counts_are_keyed_by_both_id_kinds():
    by_mal, by_anidb = compare.extract_shoko_episode_counts([
        {"IDs": {"MAL": [100], "AniDB": 200}, "Sizes": {"Local": {"Episodes": 12}}},
    ])
    assert by_mal == {"100": 12}
    assert by_anidb == {"200": 12}


def test_per_series_counts_sum_across_a_shared_mal_id():
    """Split-cour is often two Shoko series sharing one MAL ID."""
    by_mal, _ = compare.extract_shoko_episode_counts([
        {"IDs": {"MAL": [100], "AniDB": 200}, "Sizes": {"Local": {"Episodes": 12}}},
        {"IDs": {"MAL": [100], "AniDB": 201}, "Sizes": {"Local": {"Episodes": 11}}},
    ])
    assert by_mal == {"100": 23}


def test_per_series_counts_skip_series_with_no_ids():
    assert compare.extract_shoko_episode_counts([
        {"Sizes": {"Local": {"Episodes": 12}}},
    ]) == ({}, {})


def test_per_series_counts_survive_garbage_values():
    by_mal, _ = compare.extract_shoko_episode_counts([
        {"IDs": {"MAL": [100]}, "Sizes": {"Local": {"Episodes": "nonsense"}}},
    ])
    assert by_mal == {}


def test_local_episode_count_lands_on_the_entry():
    entries = compare.build_season_entries(
        [media(1, 1)], {1: {"mal_id": "100", "anidb_id": "200"}},
        mal_ids={"100"}, anidb_ids=set(),
        local_by_mal={"100": 7}, local_by_anidb={},
    )
    assert entries[0].episodes_local == 7


def test_local_episode_count_takes_the_max_not_the_sum():
    """One Shoko series exports both IDs; summing would double every count."""
    entries = compare.build_season_entries(
        [media(1, 1)], {1: {"mal_id": "100", "anidb_id": "200"}},
        mal_ids={"100"}, anidb_ids=set(),
        local_by_mal={"100": 7}, local_by_anidb={"200": 7},
    )
    assert entries[0].episodes_local == 7


def test_local_episode_count_defaults_to_zero_without_lookups():
    entries = compare.build_season_entries(
        [media(1, 1)], {1: {"mal_id": "100"}}, mal_ids=set(), anidb_ids=set(),
    )
    assert entries[0].episodes_local == 0


# ==========================
# Aired episodes
# ==========================

def test_aired_is_one_behind_the_next_airing_episode():
    assert compare.aired_episodes(media(1, 1, next_episode=6)) == 5


def test_nothing_has_aired_before_the_first_episode():
    assert compare.aired_episodes(media(1, 1, next_episode=1)) == 0


def test_aired_is_unknown_for_a_releasing_show_on_break():
    """No nextAiringEpisode between cours - "0 aired" would be a lie."""
    assert compare.aired_episodes(media(1, 1, status="RELEASING")) is None


def test_a_finished_show_has_aired_its_full_run():
    assert compare.aired_episodes(
        media(1, 1, status="FINISHED", episodes=12)
    ) == 12


def test_a_finished_show_with_no_episode_count_is_unknown():
    assert compare.aired_episodes(
        media(1, 1, status="FINISHED", episodes=None)
    ) is None


# ==========================
# Long runners
# ==========================

def test_one_piece_shape_is_a_long_runner():
    assert compare.is_long_runner(
        media(1, 1, episodes=None, next_episode=1100), today_year=2026
    ) is True


def test_sazae_san_shape_is_a_long_runner():
    assert compare.is_long_runner(
        media(1, 1, episodes=None, startDate={"year": 1969}), today_year=2026
    ) is True


def test_a_new_cour_with_no_confirmed_length_is_not_a_long_runner():
    """`episodes` is null on plenty of ordinary new shows - the floors matter."""
    assert compare.is_long_runner(
        media(1, 1, episodes=None, next_episode=5, startDate={"year": 2026}),
        today_year=2026,
    ) is False


def test_a_normal_two_cour_show_is_not_a_long_runner():
    assert compare.is_long_runner(
        media(1, 1, episodes=24, next_episode=200), today_year=2026
    ) is False


# ==========================
# Completeness
# ==========================

def test_a_show_is_complete_when_shoko_has_every_aired_episode():
    assert compare.is_complete(
        {"owned": True, "episodes_aired": 5, "episodes_local": 5}
    ) is True


def test_a_show_missing_an_aired_episode_is_incomplete():
    assert compare.is_complete(
        {"owned": True, "episodes_aired": 5, "episodes_local": 4}
    ) is False


def test_completeness_falls_back_to_ownership_when_aired_is_unknown():
    """Pre-4.3 payloads have no counts; owned has to keep meaning complete."""
    assert compare.is_complete({"owned": True}) is True
    assert compare.is_complete({"owned": False}) is False
