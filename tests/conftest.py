from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from adjud.paths import DataPaths
from adjud.store import FileCaseStore


class FakeClock:
    """Deterministic clock: starts at a fixed time and advances one second per call."""

    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 11, 5, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(seconds=1)
        return current


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    return DataPaths(tmp_path / "data")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(paths, clock) -> FileCaseStore:
    return FileCaseStore(paths, clock=clock)
