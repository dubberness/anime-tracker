"""Cron-driven scheduling, in-process.

Sleeps in short slices rather than one long sleep so a settings change or a
shutdown is picked up promptly instead of at the next fire time.
"""

import threading
from datetime import datetime

from croniter import croniter

from logging_setup import get_logger

log = get_logger(__name__)

TICK_SECONDS = 5
FALLBACK_CRON = "0 4 * * *"


class Scheduler:
    def __init__(self, config_store, runner, run_state):
        self.config = config_store
        self.runner = runner
        self.state = run_state
        self._stop = threading.Event()
        self._thread = None
        # Two expressions, deliberately: `_current_cron` is what the user
        # configured and is only ever compared against the live setting to spot
        # an edit, while `_resolved_cron` is what croniter is actually driven
        # by. They differ exactly when the configured value is invalid.
        self._current_cron = None
        self._resolved_cron = FALLBACK_CRON
        self._next_run = None

    # -- lifecycle --

    def start(self):
        self._thread = threading.Thread(
            target=self._loop, name="scheduler", daemon=True
        )
        self._thread.start()
        return self._thread

    def stop(self):
        self._stop.set()

    def wake(self):
        """Recompute the next fire time, e.g. after the schedule was edited."""
        self._current_cron = None

    # -- internals --

    def _resolve_cron(self):
        expression = self.config.settings.schedule.cron

        if not croniter.is_valid(expression):
            log.error("Invalid cron expression %r - falling back to %r",
                      expression, FALLBACK_CRON)
            expression = FALLBACK_CRON

        return expression

    def _schedule_next(self, now=None):
        now = now or datetime.now()
        # Recorded as configured, not as resolved. Storing the fallback under
        # `_current_cron` would never match settings.schedule.cron again, so
        # every tick would read as "the schedule changed", reschedule and
        # `continue` - and a run would never actually fire.
        self._current_cron = self.config.settings.schedule.cron
        self._resolved_cron = self._resolve_cron()
        self._next_run = croniter(self._resolved_cron, now).get_next(datetime)
        self.state.set_next_run(self._next_run)
        log.info("Next scheduled run: %s", self._next_run.strftime("%Y-%m-%d %H:%M:%S"))
        return self._next_run

    def _loop(self):
        self._schedule_next()

        while not self._stop.is_set():
            self._stop.wait(TICK_SECONDS)
            if self._stop.is_set():
                break

            now = datetime.now()

            # The schedule was edited, or wake() was called - recompute.
            if self._current_cron != self.config.settings.schedule.cron:
                log.info("Schedule changed - recomputing the next run")
                self._schedule_next(now)
                continue

            if self._next_run is None:
                self._schedule_next(now)
                continue

            # A clock jump backwards (DST, NTP correction) would otherwise
            # leave the next run stranded far in the future.
            if (self._next_run - now).total_seconds() > _max_gap(self._resolved_cron):
                log.warning("Clock moved backwards - recomputing the schedule")
                self._schedule_next(now)
                continue

            if now >= self._next_run:
                log.info("Scheduled run triggered")
                self.runner.run(trigger="schedule")
                self._schedule_next(datetime.now())

        log.info("Scheduler stopped")


def _max_gap(expression):
    """Longest plausible wait for this expression, with slack, in seconds."""
    try:
        base = datetime.now()
        iterator = croniter(expression, base)
        first = iterator.get_next(datetime)
        second = iterator.get_next(datetime)
        return max((second - first).total_seconds() * 2, 3600)
    except Exception:  # noqa: BLE001
        return 86400 * 2
