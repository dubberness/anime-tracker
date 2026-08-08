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
    # A prequel season of this show is already in Shoko - i.e. this is the next
    # season of something being followed, not a new franchise.
    sequel_of_owned: bool = False
    format: str = ""
    status: str = ""
    genres: List[str] = field(default_factory=list)
    # Romaji title when it differs from `title`. Release groups pick one
    # convention or the other, so autobrr wants both spellings.
    title_alt: str = ""
    tvdb_id: str = ""
    tvdb_season: Optional[int] = None
    sonarr_status: str = SONARR_UNKNOWN

    # -- airing --
    # `status` above is AniList's RELEASING/FINISHED/NOT_YET_RELEASED. These
    # add the detail needed to tell "still coming" from "done", which is what
    # the autobrr lifecycle turns on.
    episodes_aired: Optional[int] = None   # AniList: how many have aired
    episodes_local: int = 0                # Shoko: how many are on disk
    # AniList's *start* season, which is not necessarily the one it is airing
    # in - a two-cour Spring show is still tagged SPRING all through Summer.
    season: str = ""
    season_year: Optional[int] = None
    next_airing_at: Optional[int] = None   # unix ts of the next episode
    is_long_runner: bool = False

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
    # Episodes Shoko holds under this TVDB ID. A series that is in both but
    # noticeably short on Shoko's side is a half-finished move, not a done one.
    shoko_episodes: int = 0
    partial: bool = False
    # No TVDB ID, or one the mapping file has never heard of, so there is no
    # route to Shoko's AniDB IDs and no answer either way.
    unmappable: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class ShokoEntry:
    """One Shoko series and where it stands in Sonarr.

    The mirror image of SonarrEntry: the migration is only legible from both
    sides, since a series Sonarr never had still had to come from somewhere.

    `sonarr_status` reuses the SONARR_* states rather than a bool because most
    of a Shoko library - every movie, OVA and anything the mapping file hasn't
    caught up with - has no TVDB ID at all. Those are `unmapped`, meaning "no
    way to tell", and calling them Shoko-only would bury the real answers.
    """

    title: str
    anidb_id: str
    # Every TVDB ID this series resolves to. Usually one, but a series can
    # carry both its own (possibly stale) ID and one from the mapping file, and
    # either may be the one Sonarr knows it by.
    tvdb_ids: List[str] = field(default_factory=list)
    tvdb_id: str = ""
    episodes: int = 0
    sonarr_status: str = SONARR_UNKNOWN

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
