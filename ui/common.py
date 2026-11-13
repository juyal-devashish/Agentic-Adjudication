"""Shared UI helpers: data paths, the intake pipeline, demo users and the sidebar context.

Roles are SIMULATED: anyone can switch. That is acceptable only because the data is
synthetic. Every write still goes through the CaseStore rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

from adjud.intake import IntakePipeline
from adjud.paths import DataPaths
from adjud.qc import Checklist

STUDY = "DEMO01"


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

    def sees(self, subject: str | None) -> bool:
        """Site segregation: a site user sees only subjects of their own site (subject IDs
        are <study>-<site>-<number>, e.g. DEMO01-101-0001 -> site 101). Shown, not enforced: roles
        are simulated."""
        if self.role != "site":
            return True
        parts = (subject or "").split("-")
        return len(parts) > 1 and parts[1] == self.user.get("site")


def context() -> Context:
    return st.session_state["ctx"]


def demo_ready() -> bool:
    return (paths().edc_mock / "demographics.csv").exists()


@st.cache_resource
def _processor(root: str):
    from adjud.documents import DocumentProcessor

    return DocumentProcessor(_pipeline(root).store, DataPaths(Path(root)).config, STUDY)


def processor():
    return _processor(str(paths().root))


def doc_label(type_: str) -> str:
    """'ecg_report' -> 'ECG report', as the study's document checklist names it."""
    return Checklist.for_study(paths().config, STUDY).label(type_)


def study_config() -> dict[str, Any]:
    return yaml.safe_load((paths().study_config(STUDY) / "study.yaml").read_text())


def case_header(ctx: Context) -> None:
    """Case heading, lifecycle rail and the next step for whoever is looking."""
    import theme
    from wording import next_step

    from adjud.documents import documents_of
    from adjud.review import submission_status

    pipe = pipeline()
    cs = pipe.store.state(ctx.case_id)
    snap = pipe.latest_snapshot(ctx.case_id)
    theme.case_heading(
        ctx.case_id,
        snap["ae_term"],
        f"Onset {theme.date_words(snap['onset_date'])}. Subject {snap['subject_id']} at site "
        f"{snap['site_id']}." + (f" {theme.flag_words(cs.flags)}." if cs.flags else ""),
    )
    theme.lifecycle(cs.state)
    theme.next_step(
        next_step(
            ctx.role,
            cs.state,
            site=snap["site_id"],
            requested=[doc_label(r) for r in study_config().get("requested_documents", [])],
            ready=submission_status(pipe.store, ctx.case_id).ready,
            has_files=bool(documents_of(pipe.store, ctx.case_id).files),
        )
    )
