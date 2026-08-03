"""Dataclasses shared across the comparison pipeline."""

from dataclasses import asdict, dataclass, field
from typing import Any, List, Optional


@dataclass
class Entry:
    """One tracked AniList series, matched (or not) against Shoko."""

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
