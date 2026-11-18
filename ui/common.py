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
# Flows 3–4 e-mails name adjudicators or show the adjudication's progress: a site or another
# adjudicator sees only the ones addressed to it (ACC-04, P-45).
BLINDED = {"adjudication_assigned", "adj3_assigned", "consensus_to_sign", "committee_needed",
           "more_data_requested", "outcome_for_pv", "adjudication_complete",
           "readjudication_started"}  # fmt: skip


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

    def sees(self, subject: str | None, case_id: str | None = None) -> bool:
        """Site segregation: a site user sees only subjects of their own site (subject IDs
        are <study>-<site>-<number>, e.g. DEMO01-101-0001 -> site 101). An adjudicator sees
        only the cases assigned to them (ACC-02). Shown, not enforced: roles are simulated."""
        if self.role == "adjudicator":
            from adjud.flow3 import assigned_cases

            chair = bool(self.user.get("chair"))  # the Chair also sees committee cases
            return case_id is not None and case_id in assigned_cases(
                pipeline().store, self.actor, chair
            )
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


def site_name(site_id: str | None) -> str:
    """'101' -> 'Larkfield General Hospital', from `site_names` in study.yaml."""
    names = study_config().get("site_names") or {}
    return names.get(str(site_id), f"site {site_id}")


def organisation() -> str:
    return study_config().get("organisation", "")


def doc_types() -> dict[str, dict[str, Any]]:
    """The study's document types, in checklist order."""
    return Checklist.for_study(paths().config, STUDY).types


def study_config() -> dict[str, Any]:
    return yaml.safe_load((paths().study_config(STUDY) / "study.yaml").read_text())


def my_review(user_id: str, case_id: str) -> str | None:
    """'open' or 'signed' for the review this person holds in the case's current pass, or
    None when they hold none (never assigned, or reassigned away)."""
    from adjud.flow3 import Flow3, current_round

    store = pipeline().store
    slot = Flow3(store, paths().config, STUDY).slot_of(case_id, user_id)
    if slot is None:
        return None
    return "signed" if slot in current_round(store, case_id).signed else "open"


def case_header(ctx: Context, here: str) -> None:
    """Case heading with its key facts, the lifecycle rail and the next step for whoever is
    looking, with a link to the page where they take it. `here` is the current page file,
    so the link never points to the page already open."""
    import html

    import theme
    from wording import next_page, next_step

    from adjud.documents import documents_of
    from adjud.review import submission_status

    pipe = pipeline()
    cs = pipe.store.state(ctx.case_id)
    snap = pipe.latest_snapshot(ctx.case_id)
    since = f'<span class="adj-quiet">since {theme.date_words(cs.state_since)}</span>'
    facts = [
        ("Status", f"{theme.state_status(cs.state, ctx.role)} {since}"),
        ("Onset", html.escape(theme.date_words(snap["onset_date"]))),
        ("Subject", html.escape(snap["subject_id"])),
        ("Site", html.escape(site_name(snap["site_id"]))),
        ("AE", html.escape(snap["ae_id"])),
    ]
    if cs.flags:
        facts.append(("Flags", theme.status(theme.flag_words(cs.flags), "bad")))
    review = my_review(ctx.actor, ctx.case_id) if ctx.role == "adjudicator" else None
    with st.container(key="case_head"):
        theme.case_heading(ctx.case_id, snap["ae_term"], facts)
        theme.lifecycle(cs.state)
        theme.next_step(
            next_step(
                ctx.role,
                cs.state,
                site=site_name(snap["site_id"]),
                requested=[doc_label(r) for r in study_config().get("requested_documents", [])],
                ready=submission_status(pipe.store, ctx.case_id).ready,
                has_files=bool(documents_of(pipe.store, ctx.case_id).files),
                review=review,
            )
        )
        target = next_page(ctx.role, cs.state, review=review)
        if target and target[0] != here:
            st.page_link(target[0], label=target[1], icon=target[2])


def case_list(ctx: Context, intro: str) -> None:
    """Every case this person may see. Selecting a row opens the case, the same as choosing
    it in the sidebar."""
    import pandas as pd
    import theme

    states = [cs for cs in pipeline().store.list_case_states() if ctx.sees(cs.subject, cs.case_id)]
    if ctx.role == "site":
        theme.quiet(f"Only cases of {site_name(ctx.user['site'])} are shown.")
    if not states:
        st.write(
            "No cases yet. They appear here once the data manager imports AE records."
            if ctx.role != "data_manager"
            else "No cases yet. Import an AE file on the AE import page first."
        )
        return
    st.write(intro)
    ids = [cs.case_id for cs in states]
    # A new key after each pick, so a remembered selection never reopens a case the
    # person has since left in the sidebar.
    n = st.session_state.get("case_list_round", 0)
    key = f"case_list_{n}"

    def open_case() -> None:
        rows = st.session_state[key]["selection"]["rows"]
        if rows:
            st.session_state["case_id"] = ids[rows[0]]
            st.session_state["case_list_round"] = n + 1

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Case": cs.case_id,
                    "Subject": cs.subject,
                    "AE": cs.ae_id,
                    "Status": theme.state_words(cs.state, ctx.role),
                    "Since": theme.date_words(cs.state_since),
                    "Flags": theme.flag_words(cs.flags),
                }
                for cs in states
            ]
        ),
        hide_index=True,
        width="stretch",
        on_select=open_case,
        selection_mode="single-row",
        key=key,
    )


@st.cache_resource
def _workspace(root: str):
    from adjud.slack_app.core import Workspace

    return Workspace(DataPaths(Path(root)), STUDY)


TASKS_SHOWN = 5  # then "and N more", so the page's own content stays near the top


def your_tasks(ctx: Context, here: str) -> None:
    """'Your tasks': what waits for this person, the same list as Slack's App Home (P-56,
    ADR 0003), each with a button to the page where it is done, on its case. `here` is the
    current page's key: the list shows on the role's landing page only. A task with no case
    done on this page needs no button. No Clinical Content: a case ID and a step."""
    import theme
    from wording import PAGES, ROLE_PAGES

    if ROLE_PAGES[ctx.role][0] != here:
        return

    tasks = _workspace(str(paths().root)).tasks(ctx.user)
    with st.container(key="your_tasks"):
        st.markdown("**Your tasks**")
        if not tasks:
            theme.quiet("Nothing is waiting for you.")
            return
        for task in tasks[:TASKS_SHOWN]:
            case, words, action = st.columns([1.2, 4, 1.4], vertical_alignment="center")
            case.markdown(f"**{task.case_id}**" if task.case_id else "")
            words.markdown(task.words)
            if task.case_id is None and task.page == here:
                continue
            label = f"Open {task.case_id}" if task.case_id else f"Open {PAGES[task.page][1]}"
            if action.button(label, key=f"task_{task.page}_{task.case_id}"):
                if task.case_id:
                    st.session_state["case_id"] = task.case_id
                st.switch_page(PAGES[task.page][0])
        if len(tasks) > TASKS_SHOWN:
            theme.quiet(f"and {len(tasks) - TASKS_SHOWN} more")
