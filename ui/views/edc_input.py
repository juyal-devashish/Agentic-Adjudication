"""AE import: import AE files, see every record's eligibility with
rule-by-rule reasons, enter an AE by hand, and see flagged cases."""

from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd
import streamlit as st
import theme
from common import context, paths, pipeline
from wording import (
    AE_CHOICES,
    AE_FIELD_LABELS,
    ENTRY_PROBLEMS,
    IMPORT_OUTCOME,
    code_words,
    import_summary,
)

from adjud.edc_pull import pull
from adjud.events import format_time, utc_now
from adjud.intake import DATE_COLUMNS, OPTIONAL_COLUMNS, REQUIRED_COLUMNS, REQUIRED_VALUES
from adjud.store import read_log, write_atomic

ctx = context()
p = paths()
pipe = pipeline()
can_import = ctx.role == "data_manager"  # plan P-25

st.title("AE import")
st.write(
    "Adverse events from the EDC become adjudication cases only when they meet the "
    "study's rules. Every record gets an explanation, whether or not it becomes a case."
)

if not can_import:
    st.info("Importing is done by the data manager. Switch role in the sidebar to try it.")


def finish(section: str, results: list) -> None:
    """Keep the import messages and redraw, so the sidebar's case list and the queue note
    already include what was just imported."""
    st.session_state[f"import_msgs_{section}"] = [
        (r.status == "ACCEPTED", import_summary({**r.summary(), "file": r.file})) for r in results
    ]
    st.rerun()


def show_messages(section: str) -> None:
    for ok, text in st.session_state.pop(f"import_msgs_{section}", []):
        (st.success if ok else st.error)(text)


# --- pull from the EDC (plan step 21) ---------------------------------------------------------
st.header("Pull from the EDC")
st.write(
    "Fetch the study's AE records and import them in one step. Records already imported "
    "and unchanged are skipped."
)
theme.quiet("Demo stand-in: this reads the mock EDC's AE table. There is no live EDC connection.")
if st.button("Pull AE records now", disabled=not can_import):
    finish("pull", pull(pipe, ctx.actor))
show_messages("pull")

st.header("Import a file")
# --- import -------------------------------------------------------------------------------
upload = st.file_uploader(
    "AE export from the EDC (CSV or XLSX)", type=["csv", "xlsx"], disabled=not can_import
)
if st.button("Import file", type="primary", disabled=not (can_import and upload)):
    dest = p.inbox / Path(upload.name).name
    try:
        write_atomic(dest, upload.getvalue(), overwrite=False)
    except FileExistsError:
        st.error(
            f"A file named {dest.name} is already waiting to be imported. Rename it, or "
            "import the queued files first."
        )
    else:
        finish("file", pipe.process_inbox(p.inbox, p.processed, actor=ctx.actor))
show_messages("file")

waiting = sorted(f.name for f in p.inbox.glob("*") if f.is_file()) if p.inbox.exists() else []
if waiting:
    st.write(
        f"{len(waiting)} file(s) are waiting in the import queue: {', '.join(waiting)}. "
        "The runner imports them every few minutes."
    )
    if st.button("Import queued files", disabled=not can_import):
        finish("queue", pipe.process_inbox(p.inbox, p.processed, actor=ctx.actor))
show_messages("queue")

# --- results ----------------------------------------------------------------------------------
st.header("Import results")
reports = sorted(p.processed.glob("*.report.json"), key=lambda f: f.stat().st_mtime, reverse=True)
if not reports:
    st.write("Nothing has been imported yet. Import the EDC's AE export above.")
else:
    chosen = st.selectbox(
        "File", reports, format_func=lambda f: f.name.removesuffix(".report.json")
    )
    rep = json.loads(chosen.read_text())
    outcomes = [o for o in rep.get("outcomes") or [] if ctx.sees(o["key"].split("|")[1])]
    if ctx.role == "site":  # site segregation: no totals or rows from other sites
        theme.quiet(f"Only records of site {ctx.user['site']} are shown.")
    else:
        st.write(import_summary(rep))
    if rep["errors"] and ctx.role != "site":
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Row": e["row"],
                        "Column": e["column"],
                        "Problem": e["code"].replace("_", " ").capitalize(),
                        "Detail": e.get("message", ""),
                    }
                    for e in rep["errors"]
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    if outcomes:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Row": o.get("row"),
                        "Subject": o["key"].split("|")[1],
                        "AE": o["key"].split("|")[2],
                        "Result": IMPORT_OUTCOME.get(o["outcome"], o["outcome"]),
                        "Rules not met": ", ".join(o.get("failed_rules") or []),
                        "Case": o.get("case_id") or "",
                        "Flags": theme.flag_words(o.get("flags") or []),
                    }
                    for o in outcomes
                ]
            ),
            hide_index=True,
            width="stretch",
        )

        latest = {
            e["key"]: e for e in read_log(pipe.store.evaluations_path) if e.get("explanation")
        }
        keys = [o["key"] for o in outcomes]
        key = st.selectbox(
            "Why?", keys, format_func=lambda k: f"{k.split('|')[1]}, {k.split('|')[2]}"
        )
        if key in latest:
            e = latest[key]

            verdict = "meets" if e["eligible"] else "does not meet"
            st.html(
                f"<p>This record {verdict} rule set {html.escape(e['rule_set'])} "
                f"version {e['rule_version']}.</p><ul>{theme.rules_html(e['explanation'])}</ul>"
            )

# --- manual entry (F1-08) ----------------------------------------------------------------------
st.header("Enter an AE by hand")
if not can_import:
    st.write("Only the data manager can enter an AE by hand.")
else:
    st.write(
        "Use this when the EDC entry is not available yet. The record goes through the "
        "same rules and is marked as entered by hand."
    )
    with st.expander("Open the form"), st.form("manual"):
        values = {"study_id": pipe.study}
        required = set(REQUIRED_VALUES)
        grid = st.columns(3)
        fields = [c for c in AE_FIELD_LABELS if c in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
        for i, col in enumerate(c for c in fields if c != "study_id"):
            label = AE_FIELD_LABELS[col] + (" *" if col in required else "")
            box, key = grid[i % 3], f"m_{col}"
            if col in DATE_COLUMNS:
                day = box.date_input(label, value=None, format="YYYY-MM-DD", key=key)
                values[col] = day.isoformat() if day else ""
            elif col in AE_CHOICES:
                pick = box.selectbox(label, AE_CHOICES[col], index=None, key=key,
                                     format_func=code_words, placeholder="Choose")  # fmt: skip
                values[col] = pick or ""
            elif col == "last_modified":
                values[col] = box.text_input(label, value=format_time(utc_now()), key=key)
            else:
                values[col] = box.text_input(label, key=key)
        theme.quiet(f"Study {pipe.study}. Fields marked * are required.")
        reason = st.text_area(AE_FIELD_LABELS["reason"] + " *")
        if st.form_submit_button("Save AE"):
            r = pipe.manual_entry(values, reason, ctx.actor)
            if r.status == "REJECTED":
                problems = "; ".join(
                    f"{AE_FIELD_LABELS.get(e['column'] or '', e['column'])}: "
                    f"{ENTRY_PROBLEMS.get(e['code'], e['code'].replace('_', ' ').lower())}"
                    for e in r.errors
                )
                st.error(f"Not saved. Fix these fields: {problems}.")
            else:
                o = r.outcomes[0]
                st.session_state["manual_saved"] = (
                    f"Saved: {IMPORT_OUTCOME[o['outcome']].lower()}"
                    + (f", case {o['case_id']}." if o.get("case_id") else ".")
                )
                st.rerun()
    if saved := st.session_state.pop("manual_saved", None):
        st.success(saved)

# --- flagged cases ----------------------------------------------------------------------------
st.header("Cases to check")
flagged = [cs for cs in pipe.store.list_case_states() if cs.flags and ctx.sees(cs.subject)]


def why_flagged(cs) -> str:
    """The flags in words; a possible duplicate names the case it may duplicate."""
    words = []
    for flag in cs.flags:
        text = theme.flag_words([flag])
        if flag == "possible_duplicate":
            events = pipe.store.events(cs.case_id)
            marks = [e["data"] for e in events if e["type"] == "case.flagged"]
            of = next((d.get("of_case") for d in marks if d.get("flag") == flag), None)
            text += f" of {of}" if of else ""
        words.append(text if not words else text[0].lower() + text[1:])
    return "; ".join(words)


if flagged:
    st.write(
        "These cases changed in the EDC after they were created, or may duplicate another "
        "case. Nothing is withdrawn or merged automatically."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Case": cs.case_id,
                    "Subject": cs.subject,
                    "AE": cs.ae_id,
                    "Status": theme.state_words(cs.state),
                    "Why": why_flagged(cs),
                }
                for cs in flagged
            ]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("No case needs checking.")
