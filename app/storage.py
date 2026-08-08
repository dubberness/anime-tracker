"""Persistence: a SQLite time series of runs, plus the latest full result set.

Split on purpose - the per-run stats are small and worth querying over time,
while the full result set is a single blob that only ever gets replaced. The
blob lives in a plain JSON file so it stays inspectable from the config share.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime

from logging_setup import get_logger

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT    NOT NULL,
    finished_at       TEXT,
    status            TEXT    NOT NULL,
    error             TEXT,
    duration_seconds  REAL,
    tracked           INTEGER DEFAULT 0,
    owned             INTEGER DEFAULT 0,
    missing           INTEGER DEFAULT 0,
    completion        REAL    DEFAULT 0,
    shoko_shows       INTEGER DEFAULT 0,
    shoko_episodes    INTEGER DEFAULT 0,
    sonarr_shows      INTEGER DEFAULT 0,
    sonarr_migrated   INTEGER DEFAULT 0,
    sonarr_remaining_gb REAL  DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs (started_at);

-- Shows autobrr is being told to grab. IDs are stored rather than looked up
-- so ownership can be rechecked even for shows too new or too obscure to
-- appear in the tracked list at all.
CREATE TABLE IF NOT EXISTS autobrr_tracked (
    anilist_id  INTEGER PRIMARY KEY,
    title       TEXT    NOT NULL,
    title_alt   TEXT    NOT NULL DEFAULT '',
    mal_id      TEXT    NOT NULL DEFAULT '',
    anidb_id    TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'manual',
    added_at    TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT '',
    status_at   TEXT    NOT NULL DEFAULT ''
);

-- Shows deliberately untracked. Without this an auto-seeded pick would come
-- straight back on the next run.
CREATE TABLE IF NOT EXISTS autobrr_excluded (
    anilist_id  INTEGER PRIMARY KEY,
    excluded_at TEXT    NOT NULL
);
"""


class Storage:
    def __init__(self, database_file, results_file):
        self.database_file = database_file
        self.results_file = results_file
        self._lock = threading.Lock()
        self._ensure_parent(self.database_file)
        self._ensure_parent(self.results_file)
        self.init_db()

    @staticmethod
    def _ensure_parent(path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _connect(self):
        conn = sqlite3.connect(self.database_file, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
        log.debug("History database ready at %s", self.database_file)

    @staticmethod
    def _migrate(conn):
        """Add columns to tables that predate them.

        CREATE TABLE IF NOT EXISTS is a no-op against an existing database, so
        editing SCHEMA alone would leave every upgraded install on the old
        shape and failing on the first read of a new column.
        """
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(autobrr_tracked)")
        }

        for name, ddl in (("status", "TEXT NOT NULL DEFAULT ''"),
                          ("status_at", "TEXT NOT NULL DEFAULT ''")):
            if name not in existing:
                conn.execute(
                    f"ALTER TABLE autobrr_tracked ADD COLUMN {name} {ddl}"
                )
                log.info("Added autobrr_tracked.%s to the existing database", name)

    # ==========================
    # Run history
    # ==========================

    def start_run(self):
        started = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (started_at, status) VALUES (?, 'running')",
                (started,),
            )
            return cursor.lastrowid

    def finish_run(self, run_id, status, stats=None, totals=None,
                   migration=None, error=None, duration=None):
        stats = stats or {}
        totals = totals or {}
        migration = migration or {}

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE runs SET
                    finished_at = ?, status = ?, error = ?, duration_seconds = ?,
                    tracked = ?, owned = ?, missing = ?, completion = ?,
                    shoko_shows = ?, shoko_episodes = ?,
                    sonarr_shows = ?, sonarr_migrated = ?, sonarr_remaining_gb = ?
                WHERE id = ?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    status,
                    error,
                    duration,
                    stats.get("total", 0),
                    stats.get("owned", 0),
                    stats.get("missing", 0),
                    stats.get("completion", 0),
                    totals.get("shoko_shows", 0),
                    totals.get("shoko_episodes", 0),
                    totals.get("sonarr_shows", 0),
                    migration.get("migrated", 0),
                    migration.get("remaining_size_gb", 0),
                    run_id,
                ),
            )

    def history(self, limit=60):
        """Successful runs, oldest first - ready to plot."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE status = 'success'
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def recent_runs(self, limit=10):
        """Most recent runs of any status, newest first."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def last_successful_run(self):
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE status = 'success' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def prune(self, keep=500):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                DELETE FROM runs WHERE id NOT IN (
                    SELECT id FROM runs ORDER BY id DESC LIMIT ?
                )
                """,
                (keep,),
            )

    # ==========================
    # Autobrr tracking
    # ==========================

    def track_autobrr(self, anilist_id, title, title_alt="", mal_id="",
                      anidb_id="", source="manual"):
        """Add or refresh a tracked show, and clear any exclusion on it.

        Re-tracking something is an explicit "yes, I do want this", so it has
        to undo an earlier untrack rather than leaving a stale exclusion that
        would block the next auto-seed.
        """
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO autobrr_tracked
                    (anilist_id, title, title_alt, mal_id, anidb_id, source, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(anilist_id) DO UPDATE SET
                    title = excluded.title,
                    title_alt = excluded.title_alt,
                    mal_id = excluded.mal_id,
                    anidb_id = excluded.anidb_id
                """,
                (int(anilist_id), title, title_alt or "", str(mal_id or ""),
                 str(anidb_id or ""), source,
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.execute(
                "DELETE FROM autobrr_excluded WHERE anilist_id = ?",
                (int(anilist_id),),
            )

    def untrack_autobrr(self, anilist_id, exclude=False):
        """Stop tracking a show, optionally pinning it out of auto-seeding."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM autobrr_tracked WHERE anilist_id = ?",
                (int(anilist_id),),
            )
            if exclude:
                conn.execute(
                    """
                    INSERT INTO autobrr_excluded (anilist_id, excluded_at)
                    VALUES (?, ?)
                    ON CONFLICT(anilist_id) DO NOTHING
                    """,
                    (int(anilist_id),
                     datetime.now().isoformat(timespec="seconds")),
                )

    def record_autobrr_status(self, anilist_id, status):
        """Store the AniList status observed for a tracked show.

        Only a *change* restamps status_at. Re-observing FINISHED every night
        would otherwise keep pushing the grace clock forward and nothing would
        ever age out.

        Deliberately not routed through track_autobrr: that clears the
        exclusion table, which would be wrong to trigger on a status refresh.
        """
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE autobrr_tracked
                   SET status = ?, status_at = ?
                 WHERE anilist_id = ? AND status != ?
                """,
                (status, datetime.now().isoformat(timespec="seconds"),
                 int(anilist_id), status),
            )

    def list_autobrr_tracked(self):
        """Every tracked show, oldest first."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM autobrr_tracked ORDER BY added_at, anilist_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def autobrr_tracked_ids(self):
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT anilist_id FROM autobrr_tracked"
            ).fetchall()
        return {row["anilist_id"] for row in rows}

    def autobrr_excluded_ids(self):
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT anilist_id FROM autobrr_excluded"
            ).fetchall()
        return {row["anilist_id"] for row in rows}

    # ==========================
    # Latest result set
    # ==========================

    def load_results(self):
        if not os.path.exists(self.results_file):
            return None
        try:
            with open(self.results_file, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read %s: %s", self.results_file, exc)
            return None

    def save_results(self, payload):
        tmp = self.results_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, self.results_file)
        log.debug("Results written to %s", self.results_file)
