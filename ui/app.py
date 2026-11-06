"""Streamlit entry point: banner, simulated role switcher, case picker, navigation.

uv run streamlit run ui/app.py
"""

from __future__ import annotations

import streamlit as st
from common import ROLE_LABELS, Context, demo_ready, paths, pipeline, users

st.set_page_config(page_title="Adjudication — Flow 1 POC", layout="wide")

with st.sidebar:
    st.error("**SYNTHETIC DATA ONLY** — not for real patient data. Not validated.")

if not demo_ready():
    st.title("Clinical Event Adjudication — Flow 1 POC")
    st.warning(
        f"No mock EDC found in `{paths().edc_mock}`. Reset the demo first:\n\n"
        "`uv run python -m tools.reset_demo`"
    )
    st.stop()

all_users = users()
with st.sidebar:
    st.subheader("Demo user (simulated)")
    role = st.selectbox(
        "Role",
        list(ROLE_LABELS),
        format_func=ROLE_LABELS.get,
        key="role",
    )
    role_users = [u for u in all_users if u["role"] == role]
    user = st.selectbox("User", role_users, format_func=lambda u: u["name"], key=f"user_{role}")

    store = pipeline().store
    states = store.list_case_states()
    if role == "site":  # a site only sees its own cases
        states = [cs for cs in states if cs.subject and cs.subject.split("-")[1] == user["site"]]
    st.subheader("Case")
    options = [None] + [cs.case_id for cs in states]
    labels = {cs.case_id: f"{cs.case_id} · {cs.state.value}" for cs in states}
    case_id = st.selectbox(
        "Case",
        options,
        format_func=lambda c: "— none —" if c is None else labels[c],
        key="case_id",
        label_visibility="collapsed",
    )

st.session_state["ctx"] = Context(user=user, case_id=case_id)

nav = st.navigation(
    [
        st.Page("pages/edc_input.py", title="1 · EDC input", icon="📥"),
        st.Page("pages/site_documents.py", title="2 · Site documents", icon="📄"),
        st.Page("pages/results.py", title="3 · Results", icon="🧾"),
    ]
)
nav.run()
