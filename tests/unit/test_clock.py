"""Unit tests for Clock abstractions per Stage 1."""

from __future__ import annotations

import datetime

from resell.clock import Clock, FakeClock, RealClock


def test_real_clock_returns_utc() -> None:
    """RealClock must return a timezone-aware UTC datetime."""
    clock: Clock = RealClock()
    now1 = clock.now()
    now2 = clock.now()

    assert now1.tzinfo is not None
    assert now1.tzinfo == datetime.UTC
    assert now2 >= now1


def test_fake_clock_advances_on_command() -> None:
    """FakeClock holds time static until explicitly advanced."""
    start_time = datetime.datetime(2026, 11, 16, 12, 0, 0, tzinfo=datetime.UTC)
    clock = FakeClock(start_time)

    assert clock.now() == start_time

    # Advance by 30 seconds
    clock.advance(seconds=30)
    expected_30 = datetime.datetime(2026, 11, 16, 12, 0, 30, tzinfo=datetime.UTC)
    assert clock.now() == expected_30

    # Advance by timedelta
    delta = datetime.timedelta(minutes=5)
    clock.advance(delta=delta)
    expected_330 = datetime.datetime(2026, 11, 16, 12, 5, 30, tzinfo=datetime.UTC)
    assert clock.now() == expected_330


def test_fake_clock_set_time() -> None:
    """FakeClock allows setting explicit target time."""
    start_time = datetime.datetime(2026, 11, 16, 12, 0, 0, tzinfo=datetime.UTC)
    clock = FakeClock(start_time)

    target_time = datetime.datetime(2026, 12, 1, 0, 0, 0, tzinfo=datetime.UTC)
    clock.set_time(target_time)

    assert clock.now() == target_time
