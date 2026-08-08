"""Season arithmetic for the seasonal charts.

AniList buckets the year into four calendar quarters, so which season a date
falls in is pure maths - no lookup table and nothing to fetch.
"""

SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")


def season_of(date):
    """The AniList season a date falls in."""
    return SEASONS[(date.month - 1) // 3]


def shift(season, year, offset):
    """Move `offset` seasons forward (or back), rolling the year over."""
    index = SEASONS.index(season) + offset
    return SEASONS[index % len(SEASONS)], year + (index // len(SEASONS))


def window(today, before=1, after=1):
    """The seasons either side of today's, oldest first."""
    season = season_of(today)
    return [
        shift(season, today.year, offset)
        for offset in range(-before, after + 1)
    ]


def label(season, year):
    return f"{season.title()} {year}"


def index(season, year):
    """A sortable ordinal, so past/current/future is plain arithmetic."""
    return year * len(SEASONS) + SEASONS.index(season)


def is_valid(season):
    """Whether a string names one of the four seasons, case-insensitively."""
    return isinstance(season, str) and season.upper() in SEASONS
