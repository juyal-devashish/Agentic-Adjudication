"""Locations of the data folder and its fixed subfolders (mvp-flow-1.md §5)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @classmethod
    def from_env(cls) -> DataPaths:
        return cls(Path(os.environ.get("ADJUD_DATA_DIR") or "data").resolve())

    @property
    def config(self) -> Path:
        return self.root / "config"

    def study_config(self, study: str) -> Path:
        return self.config / study

    @property
    def edc_mock(self) -> Path:
        return self.root / "edc_mock"

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def processed(self) -> Path:
        return self.root / "processed"

    @property
    def intake(self) -> Path:
        return self.root / "intake"

    @property
    def cases(self) -> Path:
        return self.root / "cases"

    @property
    def outbox(self) -> Path:
        return self.root / "outbox"

    @property
    def metrics(self) -> Path:
        return self.root / "metrics"

    @property
    def logs(self) -> Path:
        return self.root / "logs"
