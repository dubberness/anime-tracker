"""Dataclasses shared across the comparison pipeline."""

from dataclasses import asdict, dataclass, field
from typing import Any, List, Optional

# How a tracked entry stands in Sonarr. Five states rather than a bool because
# "the mapping has no TVDB ID" and "Sonarr doesn't have it" mean very different
# things when the whole point is comparing two libraries.
SONARR_UNKNOWN = "unknown"      # Sonarr is off, or this run couldn't reach it
SONARR_UNMAPPED = "unmapped"    # no TVDB ID in the mapping file - can't tell
SONARR_MISSING = "missing"      # TVDB ID known, series not in Sonarr
SONARR_WANTED = "wanted"        # in Sonarr, but nothing on disk yet
SONARR_OWNED = "owned"          # in Sonarr with episode files


@dataclass
class Entry:
    """One tracked AniList series, matched (or not) against Shoko and Sonarr."""

    rank: int
    title: str
    score: int
    popularity: int
    recommendation_score: float
    episodes: Optional[int]
    year: Optional[int]
    anilist_id: int
    mal_id: str
    anidb_id: str
    image: str
    owned: bool
    is_franchise_root: bool
    format: str = ""
    status: str = ""
    genres: List[str] = field(default_factory=list)
    tvdb_id: str = ""
    tvdb_season: Optional[int] = None
    sonarr_status: str = SONARR_UNKNOWN

    def to_dict(self):
        return asdict(self)


@dataclass
class SonarrEntry:
    """One Sonarr series and whether Shoko already has it."""

    title: str
    tvdb_id: Any
    status: str
    episode_file_count: int
    episode_count: int
    size_gb: float
    migrated: bool

    def to_dict(self):
        return asdict(self)


@dataclass
class Diff:
    """What changed between the previous run and this one."""

    has_previous: bool = False
    newly_owned: List[dict] = field(default_factory=list)
    newly_missing: List[dict] = field(default_factory=list)
    newly_tracked: List[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)
