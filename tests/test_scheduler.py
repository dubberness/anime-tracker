"""Schedule resolution, and the change-detection the loop turns on."""

import config as config_mod
from scheduler import FALLBACK_CRON, Scheduler


class FakeStore:
    """Just enough of ConfigStore to drive the scheduler."""

    def __init__(self, cron):
        self.settings = config_mod.Settings()
        self.settings.schedule.cron = cron


class FakeState:
    def __init__(self):
        self.next_run = None

    def set_next_run(self, when):
        self.next_run = when


def build(cron):
    store = FakeStore(cron)
    scheduler = Scheduler(store, runner=None, run_state=FakeState())
    scheduler._schedule_next()
    return store, scheduler


def changed(store, scheduler):
    """The comparison _loop makes every tick to decide whether to reschedule."""
    return scheduler._current_cron != store.settings.schedule.cron


def test_a_valid_cron_schedules_and_reads_as_unchanged():
    store, scheduler = build("0 4 * * *")

    assert scheduler._next_run is not None
    assert not changed(store, scheduler)


def test_an_invalid_cron_falls_back_without_wedging_the_loop():
    """The fallback drives croniter, but the *configured* value is what the
    loop compares against.

    Recording the fallback under _current_cron instead made every tick read as
    "the schedule changed": it rescheduled and `continue`d forever, so the
    fire check was never reached and no run ever happened.
    """
    store, scheduler = build("every night")

    assert scheduler._resolved_cron == FALLBACK_CRON
    assert scheduler._next_run is not None
    assert not changed(store, scheduler)


def test_editing_the_schedule_is_still_detected():
    store, scheduler = build("0 4 * * *")

    store.settings.schedule.cron = "30 2 * * *"

    assert changed(store, scheduler)


def test_wake_forces_a_recompute():
    store, scheduler = build("0 4 * * *")

    scheduler.wake()

    assert changed(store, scheduler)


def test_an_invalid_cron_still_notices_a_later_correction():
    store, scheduler = build("every night")

    store.settings.schedule.cron = "0 4 * * *"
    assert changed(store, scheduler)

    scheduler._schedule_next()
    assert scheduler._resolved_cron == "0 4 * * *"
    assert not changed(store, scheduler)
