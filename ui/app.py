"""Streamlit entry point: synthetic-data band, who you are, which case, navigation.

uv run streamlit run ui/app.py
"""

from __future__ import annotations

import streamlit as st
import theme
from common import Context, demo_ready, paths, pipeline, users
from wording import ROLE_NAMES

st.set_page_config(
    page_title="Event adjudication intake", layout="wide", initial_sidebar_state="auto"
)
theme.apply()
theme.synthetic_band()

if not demo_ready():
    st.title("Event adjudication intake")
    st.warning(
        f"There is no demo data in {paths().root} yet. Reset the demo to load the mock EDC "
        "and the example files:\n\n`uv run python -m tools.reset_demo`"
    )
    st.stop()

all_users = users()
# Deep links for demos and screenshots, applied once per session:
# ?role=site&user=site104.coord&case=DEMO01-0007
qp = st.query_params
if "role" in qp and "role" not in st.session_state and qp["role"] in ROLE_NAMES:
    st.session_state["role"] = qp["role"]
if "case" in qp and "case_id" not in st.session_state:
    st.session_state["case_id"] = qp["case"]
if "user" in qp and "role" in st.session_state:
    match = [
        u for u in all_users if u["id"] == qp["user"] and u["role"] == st.session_state["role"]
    ]
    if match and f"user_{match[0]['role']}" not in st.session_state:
        st.session_state[f"user_{match[0]['role']}"] = match[0]
with st.sidebar:
    st.subheader("You are")
    role = st.selectbox(
        "Role",
        list(ROLE_NAMES),
        format_func=ROLE_NAMES.get,
        key="role",
        help="Roles are simulated for the demo; anyone can switch.",
    )
    role_users = [u for u in all_users if u["role"] == role]
    user = st.selectbox("Name", role_users, format_func=lambda u: u["name"], key=f"user_{role}")

    states = pipeline().store.list_case_states()
    if role == "site":  # a site sees only its own cases
        states = [cs for cs in states if cs.subject and cs.subject.split("-")[1] == user["site"]]
    st.subheader("Case")
    labels = {cs.case_id: f"{cs.case_id}, {theme.short_state(cs.state)}" for cs in states}
    if st.session_state.get("case_id") not in (None, *labels):
        st.session_state["case_id"] = None  # e.g. a deep link to another site's case
    case_id = st.selectbox(
        "Case",
        list(labels),
        index=None,
        key="case_id",
        label_visibility="collapsed",
        placeholder="Choose a case",
        format_func=labels.get,
    )
    if not states:
        st.caption("No cases yet." if role != "site" else "No cases for your site yet.")

st.session_state["ctx"] = Context(user=user, case_id=case_id)

st.navigation(
    [
        st.Page(
            "views/edc_input.py",
            title="AE import",
            icon=":material/move_to_inbox:",
            url_path="import",
            default=True,
        ),
        st.Page(
            "views/site_documents.py",
            title="Documents",
            icon=":material/description:",
            url_path="documents",
        ),
        st.Page(
            "views/results.py", title="Case record", icon=":material/fact_check:", url_path="record"
        ),
    ]
).run()
