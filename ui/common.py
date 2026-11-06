"""Shared UI helpers: data paths, the intake pipeline, demo users and the sidebar context.

Roles are SIMULATED: anyone can switch. That is acceptable only because the data is
synthetic (mvp-flow-1.md §2). Every write still goes through the CaseStore rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

from adjud.intake import IntakePipeline
from adjud.paths import DataPaths

STUDY = "DEMO01"
ROLE_LABELS = {
    "site": "Site user",
    "safety_specialist": "Safety Specialist",
    "study_admin": "Study admin",
}


def paths() -> DataPaths:
    return DataPaths.from_env()


@st.cache_resource
def _pipeline(root: str) -> IntakePipeline:
    p = DataPaths(Path(root))
    return IntakePipeline(p.root, p.config, p.edc_mock, STUDY)


def pipeline() -> IntakePipeline:
    return _pipeline(str(paths().root))


def users() -> list[dict[str, Any]]:
    return yaml.safe_load((paths().config / "users.yaml").read_text())["users"]


@dataclass(frozen=True)
class Context:
    user: dict[str, Any]
    case_id: str | None

    @property
    def role(self) -> str:
        return self.user["role"]

    @property
    def actor(self) -> str:
        return self.user["id"]


def context() -> Context:
    return st.session_state["ctx"]


def demo_ready() -> bool:
    return (paths().edc_mock / "demographics.csv").exists()
