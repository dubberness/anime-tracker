"""Logging configuration.

Everything goes to stdout so `docker logs` is the single place to look. A
ring buffer of recent records is also kept in memory so the web UI can show
the tail of the log without needing a file mount.
"""

import collections
import logging
import os
import sys
import threading

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_RING_SIZE = 500


class RingBufferHandler(logging.Handler):
    """Keeps the most recent records in memory for the /api/logs endpoint."""

    def __init__(self, capacity=_RING_SIZE):
        super().__init__()
        self._records = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record):
        try:
            entry = {
                "time": self.formatter.formatTime(record, DATE_FORMAT),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                entry["message"] += "\n" + self.format(record).split("\n", 1)[-1]
        except Exception:  # noqa: BLE001 - logging must never raise
            return

        with self._lock:
            self._records.append(entry)

    def tail(self, limit=200):
        with self._lock:
            records = list(self._records)
        return records[-limit:]


ring_handler = RingBufferHandler()


def configure(level=None):
    """Set up root logging. Safe to call more than once."""
    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    resolved = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(resolved)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    ring_handler.setFormatter(formatter)
    root.addHandler(ring_handler)

    # These are noisy at INFO and say nothing we don't already log ourselves.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    return root


def get_logger(name):
    return logging.getLogger(name)
