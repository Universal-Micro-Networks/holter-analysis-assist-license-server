from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes.sqlite_repository import SqliteRepository


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))


@pytest.fixture
def repo() -> SqliteRepository:
    return SqliteRepository()
