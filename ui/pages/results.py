"""Page 3 — Results (mvp-flow-1.md §9.3): case list, timeline with chain check, source
data, notifications. Handoff and metrics arrive in steps 4–6."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st
from common import context, paths, pipeline

from adjud.integrity import verify_case_log

ctx = context()
pipe = pipeline()
store = pipe.store

st.title("3 · Results")

states = store.list_case_states()
st.subheader(f"Cases ({len(states)})")
if states:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "case": cs.case_id,
                    "subject": cs.subject,
                    "AE": cs.ae_id,
                    "state": cs.state.value,
                    "since": cs.state_since,
                    "flags": ", ".join(cs.flags),
                }
                for cs in states
            ]
        ),
        hide_index=True,
        width="stretch",
    )

if ctx.case_id is None:
    st.info("Pick a case in the sidebar to see its timeline.")
    st.stop()

case_dir = store.case_dir(ctx.case_id)
report = verify_case_log(case_dir)
st.subheader(f"Timeline — {ctx.case_id}")
if report.ok:
    st.success(f"Chain verified ✓ ({report.records} events, every referenced file matches)")
else:
    st.error("Chain verification FAILED:\n\n" + "\n".join(f"- {p}" for p in report.problems))

events = store.events(ctx.case_id)
st.dataframe(
    pd.DataFrame(
        [
            {
                "#": e["seq"],
                "at": e["at"],
                "actor": e["actor"],
                "event": e["type"],
                "reason": e.get("reason") or "",
                "hash": e["hash"][7:19],
            }
            for e in events
        ]
    ),
    hide_index=True,
    width="stretch",
)

st.subheader("Source data")
tabs = st.tabs(["AE snapshot", "Eligibility", "Related data", "Prior events"])
with tabs[0]:
    st.json(pipe.latest_snapshot(ctx.case_id))
with tabs[1]:
    st.json(store.get_json(ctx.case_id, "source/eligibility_v1.json"), expanded=1)
with tabs[2]:
    st.json(store.get_json(ctx.case_id, "source/related_data.json"))
with tabs[3]:
    st.json(store.get_json(ctx.case_id, "source/prior_events.json"))

st.subheader("Notifications")
notes = [json.loads(f.read_text()) for f in sorted(paths().outbox.glob("*.json"))]
mine = [n for n in notes if n.get("case_id") == ctx.case_id]
if mine:
    st.dataframe(
        pd.DataFrame(
            [{k: n[k] for k in ("created_at", "to", "template", "subject")} for n in mine]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("None.")
