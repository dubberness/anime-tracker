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
        log.debug("History database ready at %s", self.database_file)

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
