"""Thread-safe view of what the app is doing right now.

The scheduler thread, any web-triggered run and every HTTP request all touch
this, so all access goes through the lock. `try_begin` is the mutual exclusion
that stops a manual "Run now" from overlapping a scheduled run.
"""

import threading
from datetime import datetime

PHASES = [
    "mappings",
    "anilist",
    "shoko",
    "sonarr",
    "compare",
    "seasons",
    "autobrr",
    "persist",
]


class RunState:
    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._phase = None
        self._message = ""
        self._started_at = None
        self._trigger = None
        self._last_finished_at = None
        self._last_error = None
        self._last_duration = None
        self._next_run_at = None
        self._run_count = 0

    # -- mutual exclusion --

    def try_begin(self, trigger="manual"):
        """Claim the run slot. False means a run is already in flight."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._phase = None
            self._message = "Starting"
            self._started_at = datetime.now()
            self._trigger = trigger
            self._last_error = None
            return True

    def finish(self, error=None):
        with self._lock:
            self._running = False
            self._phase = None
            self._message = ""
            finished = datetime.now()
            if self._started_at:
                self._last_duration = (finished - self._started_at).total_seconds()
            self._last_finished_at = finished
            self._last_error = error
            self._run_count += 1

    # -- progress --

    def set_phase(self, phase, message=""):
        with self._lock:
            self._phase = phase
            self._message = message

    def set_message(self, message):
        with self._lock:
            self._message = message

    def set_next_run(self, when):
        with self._lock:
            self._next_run_at = when

    # -- reads --

    @property
    def is_running(self):
        with self._lock:
            return self._running

    def snapshot(self):
        with self._lock:
            elapsed = None
            if self._running and self._started_at:
                elapsed = round(
                    (datetime.now() - self._started_at).total_seconds(), 1
                )

            return {
                "running": self._running,
                "phase": self._phase,
                "phases": PHASES,
                "message": self._message,
                "trigger": self._trigger,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "elapsed_seconds": elapsed,
                "last_finished_at": (
                    self._last_finished_at.isoformat()
                    if self._last_finished_at else None
                ),
                "last_duration_seconds": self._last_duration,
                "last_error": self._last_error,
                "next_run_at": (
                    self._next_run_at.isoformat() if self._next_run_at else None
                ),
                "run_count": self._run_count,
            }
