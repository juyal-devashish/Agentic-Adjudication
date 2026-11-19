"""Streamlit entry point: synthetic-data band, who you are, which case, navigation.

uv run streamlit run ui/app.py
"""

from __future__ import annotations

import mail_toasts
import streamlit as st
import theme
from common import Context, demo_ready, organisation, paths, pipeline, site_name, users
from wording import PAGES, ROLE_NAMES, ROLE_PAGES

st.set_page_config(
    page_title="Clinical event adjudication", layout="wide", initial_sidebar_state="auto"
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
elif "case" in qp and st.session_state.get("role") == "site":
    # E-mail links name the case, not the person: open it as a user of the case's site.
    subject = next(
        (cs.subject for cs in pipeline().store.list_case_states() if cs.case_id == qp["case"]),
        None,
    )
    site = subject.split("-")[1] if subject else None
    match = [u for u in all_users if u["role"] == "site" and u.get("site") == site]
else:
    match = []
if match and f"user_{match[0]['role']}" not in st.session_state:
    st.session_state[f"user_{match[0]['role']}"] = match[0]
theme.brand(organisation())
menu = st.sidebar.container()  # filled below, once the pages are registered
with st.sidebar:
    st.subheader("You are")
    # The chosen role lives in session state, copied there by the picker's callback before
    # the rerun: switching role can send the person to another page, and a page switch
    # drops a widget's new value, so the role snapped back. Unkeyed, the picker lost the
    # second switch in a row when no page switch happened.
    roles = list(ROLE_NAMES)

    def _pick_role() -> None:
        st.session_state["role"] = st.session_state["role_picker"]

    st.session_state.setdefault("role", roles[0])
    if st.session_state.get("role_picker") != st.session_state["role"]:
        st.session_state["role_picker"] = st.session_state["role"]  # e.g. after a page switch
    role = st.selectbox(
        "Role",
        roles,
        format_func=ROLE_NAMES.get,
        help="Roles are simulated for the demo; anyone can switch.",
        key="role_picker",
        on_change=_pick_role,
    )
    st.session_state["role"] = role
    role_users = [u for u in all_users if u["role"] == role]
    user = st.selectbox("Name", role_users, format_func=lambda u: u["name"], key=f"user_{role}")
    st.caption(site_name(user["site"]) if role == "site" else organisation())

    states = pipeline().store.list_case_states()
    states = [cs for cs in states if Context(user, None).sees(cs.subject, cs.case_id)]
    st.subheader("Case")
    labels = {cs.case_id: f"{cs.case_id}, {theme.short_state(cs.state, role)}" for cs in states}
    chosen = st.session_state.get("case_id")
    if chosen not in labels:
        chosen = None  # e.g. a deep link to another site's case
    # The chosen case lives in session state, not in the widget: the labels carry the case's
    # state, and when that changes between clicks the browser sends back the old label,
    # which no longer matches, so a keyed picker fell back to "Choose a case" and the click
    # (e.g. Sign and submit) was lost. Unkeyed, the picker is rebuilt from `chosen`.
    ids = list(labels)
    case_id = st.selectbox(
        "Case",
        ids,
        index=ids.index(chosen) if chosen else None,
        label_visibility="collapsed",
        placeholder="Choose a case",
        format_func=labels.get,
    )
    st.session_state["case_id"] = case_id
    if not states:
        st.caption("No cases yet." if role != "site" else "No cases for your site yet.")

st.session_state["ctx"] = Context(user=user, case_id=case_id)
mail_toasts.show()  # a pop-up for each e-mail the runner sends about a visible case
# Keep who and which case in the URL, so a browser refresh comes back to the same place.
st.query_params.update({"role": role, "user": user["id"]})
if case_id:
    st.query_params["case"] = case_id
else:
    st.query_params.pop("case", None)

# Each role sees only the pages for its own work (ROLE_PAGES); the first is where it lands.
# Every page stays registered, so links in e-mails and the next-step box always resolve;
# the menu is drawn here with the role's pages only, and a page that is not the role's
# (a typed URL, or a role switched while on it) sends the person to their own first page.
# The old Assistant chat is gone (P-58 replaced it with the Agent in Slack): its URL,
# /assistant, opens the default page below, which hands over to the role's first page.


def landing() -> None:
    """The bare address: replaced at once by the role's first page."""


pages = {
    key: st.Page(file, title=title, icon=icon, url_path=key)
    for key, (file, title, icon) in PAGES.items()
}
current = st.navigation(
    [st.Page(landing, title="Start", url_path="start", default=True), *pages.values()],
    position="hidden",
)
own = ROLE_PAGES[role]
if current.url_path not in own:
    st.switch_page(pages[own[0]])
with menu:
    for key in own:
        st.page_link(pages[key], label=PAGES[key][1], icon=PAGES[key][2])
    st.divider()


def _reset_demo() -> None:
    """Start the demo over for everyone: the same reset as scripts/slack-demo.sh start
    (the short demo, the AE file queued), without restarting the runner or the Slack app."""
    from tools.reset_demo import DEMO_AES, reset

    reset(paths(), preload=True, only=DEMO_AES)
    st.cache_resource.clear()
    st.cache_data.clear()
    for key in list(st.session_state):
        del st.session_state[key]
    st.query_params.clear()


with st.sidebar.popover("Reset the demo", icon=":material/restart_alt:"):
    st.caption(
        "Removes every case, document, signature, e-mail record and Slack conversation, "
        "for everyone using the demo, and queues the AE file again."
    )
    if st.button("Reset everything", type="primary", key="reset_demo"):
        _reset_demo()
        st.switch_page(pages[ROLE_PAGES[roles[0]][0]])
current.run()
