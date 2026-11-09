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
from wording import IMPORT_OUTCOME, import_summary

from adjud.intake import OPTIONAL_COLUMNS, REQUIRED_COLUMNS
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
        for r in pipe.process_inbox(p.inbox, p.processed, actor=ctx.actor):
            (st.success if r.status == "ACCEPTED" else st.error)(
                import_summary({**r.summary(), "file": r.file})
            )

waiting = sorted(f.name for f in p.inbox.glob("*") if f.is_file()) if p.inbox.exists() else []
if waiting:
    st.write(
        f"{len(waiting)} file(s) are waiting in the import queue: {', '.join(waiting)}. "
        "The runner imports them every few minutes."
    )
    if st.button("Import queued files", disabled=not can_import):
        for r in pipe.process_inbox(p.inbox, p.processed, actor=ctx.actor):
            (st.success if r.status == "ACCEPTED" else st.error)(
                import_summary({**r.summary(), "file": r.file})
            )

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
    st.write(import_summary(rep))
    if rep["errors"]:
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
    if rep.get("outcomes"):
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
                    for o in rep["outcomes"]
                ]
            ),
            hide_index=True,
            width="stretch",
        )

        latest = {
            e["key"]: e for e in read_log(pipe.store.evaluations_path) if e.get("explanation")
        }
        keys = [o["key"] for o in rep["outcomes"]]
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
        values = {}
        grid = st.columns(3)
        for i, col in enumerate(REQUIRED_COLUMNS + OPTIONAL_COLUMNS):
            default = "DEMO01" if col == "study_id" else ""
            values[col] = grid[i % 3].text_input(
                col.replace("_", " ").capitalize(), value=default, key=f"m_{col}"
            )
        reason = st.text_area("Why is this entered by hand?")
        if st.form_submit_button("Save AE"):
            r = pipe.manual_entry(values, reason, ctx.actor)
            if r.status == "REJECTED":
                problems = "; ".join(
                    f"{(e['column'] or '').replace('_', ' ')}: "
                    f"{e['code'].replace('_', ' ').lower()}"
                    for e in r.errors
                )
                st.error(f"Not saved. Fix these fields: {problems}.")
            else:
                o = r.outcomes[0]
                st.success(
                    f"Saved: {IMPORT_OUTCOME[o['outcome']].lower()}"
                    + (f", case {o['case_id']}." if o.get("case_id") else ".")
                )

# --- flagged cases ----------------------------------------------------------------------------
st.header("Cases to check")
flagged = [cs for cs in pipe.store.list_case_states() if cs.flags]
if flagged:
    st.write("These cases changed after they were created. Nothing is withdrawn automatically.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Case": cs.case_id,
                    "Subject": cs.subject,
                    "AE": cs.ae_id,
                    "Status": theme.state_words(cs.state),
                    "Why": theme.flag_words(cs.flags),
                }
                for cs in flagged
            ]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("No case needs checking.")
