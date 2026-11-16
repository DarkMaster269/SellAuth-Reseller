"""Clock abstraction for deterministic testing across all time-dependent logic."""

from __future__ import annotations

import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Protocol for time retrieval."""

    def now(self) -> datetime.datetime:
        """Return the current datetime with timezone (UTC)."""
        ...


class RealClock:
    """Production clock returning real-world UTC time."""

    def now(self) -> datetime.datetime:
        """Return real current UTC datetime."""
        return datetime.datetime.now(datetime.UTC)


class FakeClock:
    """Deterministic in-memory clock for unit and chaos tests."""

    def __init__(self, initial_time: datetime.datetime | None = None) -> None:
        """Initialize FakeClock with a specific or default UTC datetime."""
        if initial_time is None:
            self._current = datetime.datetime.now(datetime.UTC)
        else:
            if initial_time.tzinfo is None:
                self._current = initial_time.replace(tzinfo=datetime.UTC)
            else:
                self._current = initial_time

    def now(self) -> datetime.datetime:
        """Return the current simulated UTC datetime."""
        return self._current

    def advance(
        self,
        seconds: float | None = None,
        delta: datetime.timedelta | None = None,
    ) -> None:
        """Advance simulated clock by seconds or timedelta."""
        if delta is not None:
            self._current += delta
        elif seconds is not None:
            self._current += datetime.timedelta(seconds=seconds)

    def set_time(self, new_time: datetime.datetime) -> None:
        """Set simulated clock directly to a target datetime."""
        if new_time.tzinfo is None:
            self._current = new_time.replace(tzinfo=datetime.UTC)
        else:
            self._current = new_time
