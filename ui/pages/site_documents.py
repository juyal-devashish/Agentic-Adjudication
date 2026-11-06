"""Page 2 — Site documents (mvp-flow-1.md §9.2). Upload and review arrive in step 6."""

from __future__ import annotations

import streamlit as st
from common import context, pipeline

ctx = context()
st.title("2 · Site documents")
if ctx.case_id is None:
    st.info("Pick a case in the sidebar.")
    st.stop()

pipe = pipeline()
cs = pipe.store.state(ctx.case_id)
snap = pipe.latest_snapshot(ctx.case_id)
cols = st.columns(4)
cols[0].metric("Subject (study ID)", snap["subject_id"])
cols[1].metric("Event", snap["ae_term"])
cols[2].metric("Onset", snap["onset_date"])
cols[3].metric("State", cs.state.value)
st.info("Document upload, de-identification and review are built in plan steps 4–6.")
