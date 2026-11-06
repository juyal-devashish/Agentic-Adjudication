"""Page 1 — EDC input (mvp-flow-1.md §9.1): upload an AE file or Import now; see every
record's eligibility with rule-by-rule reasons; manual entry; flagged cases."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st
from common import context, paths, pipeline

from adjud.intake import OPTIONAL_COLUMNS, REQUIRED_COLUMNS
from adjud.rules import explain
from adjud.store import read_log, write_atomic

ctx = context()
p = paths()
pipe = pipeline()
can_import = ctx.role in ("study_admin", "safety_specialist")

st.title("1 · EDC input")
st.caption("AE records from the (mock) EDC become cases only if they meet the study's rules.")

if not can_import:
    st.info("Switch to the Study admin or Safety Specialist role to import AE files.")

# --- upload / import --------------------------------------------------------------------
left, right = st.columns([3, 1])
with left:
    upload = st.file_uploader(
        "AE file (CSV or XLSX)", type=["csv", "xlsx"], disabled=not can_import
    )
    if upload is not None and st.button("Put in inbox", disabled=not can_import):
        dest = p.inbox / Path(upload.name).name
        try:
            write_atomic(dest, upload.getvalue(), overwrite=False)
            st.success(f"{dest.name} is in the inbox. Press **Import now** or wait for the runner.")
        except FileExistsError:
            st.error(f"{dest.name} is already waiting in the inbox.")
with right:
    waiting = sorted(f.name for f in p.inbox.glob("*") if f.is_file()) if p.inbox.exists() else []
    st.metric("Files in inbox", len(waiting))
    if st.button("Import now", type="primary", disabled=not (can_import and waiting)):
        results = pipe.process_inbox(p.inbox, p.processed, actor=ctx.actor)
        for r in results:
            (st.success if r.status == "ACCEPTED" else st.error)(
                f"{r.file}: {r.status} — {r.records} records, {r.new_cases} new cases"
            )

# --- latest import reports -----------------------------------------------------------------
st.subheader("Import results")
reports = sorted(p.processed.glob("*.report.json"), key=lambda f: f.stat().st_mtime, reverse=True)
if not reports:
    st.write("No files imported yet.")
else:
    chosen = st.selectbox(
        "Report", reports, format_func=lambda f: f.name.removesuffix(".report.json")
    )
    rep = json.loads(chosen.read_text())
    cols = st.columns(5)
    cols[0].metric("Status", rep["status"])
    cols[1].metric("Records", rep["records"])
    cols[2].metric("New cases", rep["new_cases"])
    cols[3].metric("Not eligible", rep["not_eligible"])
    cols[4].metric("Changed / stale", f"{rep['changed']} / {rep['stale']}")
    if rep["errors"]:
        st.error("The file was rejected whole. Nothing was imported.")
        st.dataframe(pd.DataFrame(rep["errors"]), hide_index=True, width="stretch")
    if rep.get("outcomes"):
        table = pd.DataFrame(
            [
                {
                    "row": o.get("row"),
                    "AE key": o["key"],
                    "outcome": o["outcome"],
                    "eligible": o.get("eligible"),
                    "failed rules": ", ".join(o.get("failed_rules") or []),
                    "case": o.get("case_id") or "",
                    "flags": ", ".join(o.get("flags") or []),
                }
                for o in rep["outcomes"]
            ]
        )
        st.dataframe(table, hide_index=True, width="stretch")

        # Rule-by-rule explanation from the evaluation log (the source of truth).
        latest = {}
        for e in read_log(pipe.store.evaluations_path):
            if e.get("explanation"):
                latest[e["key"]] = e
        key = st.selectbox("Explain a record", [o["key"] for o in rep["outcomes"]])
        if key in latest:
            e = latest[key]
            st.markdown(
                f"**{'Eligible' if e['eligible'] else 'Not eligible'}** under "
                f"`{e['rule_set']}` v{e['rule_version']}"
            )
            st.code("\n".join(explain({"results": e["explanation"]})), language=None)

# --- manual entry (F1-08) -------------------------------------------------------------------
with st.expander("Manual AE entry (reason required)", expanded=False):
    if not can_import:
        st.info("Only the Study admin or Safety Specialist can enter an AE manually.")
    else:
        with st.form("manual"):
            values = {}
            grid = st.columns(3)
            for i, col in enumerate(REQUIRED_COLUMNS + OPTIONAL_COLUMNS):
                default = "DEMO01" if col == "study_id" else ""
                values[col] = grid[i % 3].text_input(col, value=default)
            reason = st.text_area("Reason for manual entry")
            if st.form_submit_button("Evaluate and save"):
                r = pipe.manual_entry(values, reason, ctx.actor)
                if r.status == "REJECTED":
                    st.error(
                        "Rejected: " + "; ".join(f"{e['column']}: {e['code']}" for e in r.errors)
                    )
                else:
                    o = r.outcomes[0]
                    st.success(f"{o['outcome']}: {o['key']} {o.get('case_id') or ''}")

# --- flagged cases --------------------------------------------------------------------------
st.subheader("Flagged cases")
flagged = [cs for cs in pipe.store.list_case_states() if cs.flags]
if flagged:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "case": cs.case_id,
                    "AE": cs.ae_id,
                    "state": cs.state.value,
                    "flags": ", ".join(cs.flags),
                }
                for cs in flagged
            ]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("No flagged cases.")
