"""Season arithmetic for the seasonal charts."""

from datetime import date

from core import seasons


def test_every_month_lands_in_the_right_season():
    assert seasons.season_of(date(2026, 1, 15)) == "WINTER"
    assert seasons.season_of(date(2026, 3, 31)) == "WINTER"
    assert seasons.season_of(date(2026, 4, 1)) == "SPRING"
    assert seasons.season_of(date(2026, 8, 3)) == "SUMMER"
    assert seasons.season_of(date(2026, 10, 1)) == "FALL"
    assert seasons.season_of(date(2026, 12, 31)) == "FALL"


def test_window_is_the_season_either_side_oldest_first():
    assert seasons.window(date(2026, 8, 3)) == [
        ("SPRING", 2026), ("SUMMER", 2026), ("FALL", 2026),
    ]


def test_window_rolls_back_over_the_new_year():
    assert seasons.window(date(2026, 2, 10)) == [
        ("FALL", 2025), ("WINTER", 2026), ("SPRING", 2026),
    ]


def test_window_rolls_forward_over_the_new_year():
    assert seasons.window(date(2026, 11, 20)) == [
        ("SUMMER", 2026), ("FALL", 2026), ("WINTER", 2027),
    ]


def test_shift_wraps_in_both_directions():
    assert seasons.shift("WINTER", 2026, -1) == ("FALL", 2025)
    assert seasons.shift("FALL", 2026, 1) == ("WINTER", 2027)
    assert seasons.shift("SUMMER", 2026, 4) == ("SUMMER", 2027)
    assert seasons.shift("SUMMER", 2026, -4) == ("SUMMER", 2025)


def test_label_is_human_readable():
    assert seasons.label("SUMMER", 2026) == "Summer 2026"


def test_index_orders_seasons_within_a_year():
    assert seasons.index("WINTER", 2026) < seasons.index("SUMMER", 2026)


def test_index_orders_across_the_year_boundary():
    assert seasons.index("FALL", 2025) < seasons.index("WINTER", 2026)


def test_is_valid_accepts_the_four_seasons_in_any_case():
    assert seasons.is_valid("WINTER") is True
    assert seasons.is_valid("summer") is True


def test_is_valid_rejects_anything_else():
    assert seasons.is_valid("AUTUMN") is False
    assert seasons.is_valid("") is False
    assert seasons.is_valid(None) is False
