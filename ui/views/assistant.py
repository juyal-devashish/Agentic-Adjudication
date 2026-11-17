"""Assistant (P-40): the guided chat everyone lands on.

Ask in plain words or with a slash action; the answers come from the case records (see
ui/assistant.py). While the page is open, what happens to the person's cases appears in
the chat as it happens. Every manual step is a button to the page where it is done.
"""

from __future__ import annotations

import streamlit as st
import theme
from assistant import PAGES, Action, Assistant
from common import context

ctx = context()
helper = Assistant(ctx.user)
HISTORY = f"assistant_chat_{ctx.user['id']}"
SEEN = f"assistant_seen_{ctx.user['id']}"
STARTERS = {
    "data_manager": ["/tasks", "/import", "/why AE-008"],
    "site": ["/tasks", "/status 0001"],
    "safety_specialist": ["/tasks", "/qc 0001"],
}
STARTER_WORDS = {"/tasks": "What's waiting for me?", "/import": "Import the AE records",
                 "/why AE-008": "Why is AE-008 not a case?", "/status 0001": "Where is case 0001?",
                 "/qc 0001": "Summarise the quality check of 0001"}  # fmt: skip

if HISTORY not in st.session_state:
    st.session_state[HISTORY] = [helper.greeting().as_dict()]
    st.session_state[SEEN] = {cs.case_id: cs.seq for cs in helper.cases()}

st.title("Assistant")
theme.quiet(
    "Answers come from the case records. Where the AI wrote something (the AI Suggestion, "
    "the narrative draft), it says so. Signatures and decisions stay with you, on the "
    "pages the buttons open."
)


def ask(text: str) -> None:
    st.session_state[HISTORY].append({"role": "user", "text": text, "actions": []})
    st.session_state[HISTORY].append(helper.handle(text).as_dict())
    # The answer already reflects the cases as they are now (an import lists its new
    # cases), so live updates continue from here instead of repeating them.
    st.session_state[SEEN] = {cs.case_id: cs.seq for cs in helper.cases()}


def open_page(action: dict) -> None:
    if action.get("case_id"):
        st.session_state["case_id"] = action["case_id"]
    st.switch_page(PAGES[action["page"]])


@st.fragment(run_every=3)
def conversation() -> None:
    """The chat, redrawn every few seconds with anything new on this person's cases."""
    for reply in helper.updates(st.session_state[SEEN]):
        st.session_state[HISTORY].append(reply.as_dict())
    history = st.session_state[HISTORY]
    for i, msg in enumerate(history):
        with st.chat_message(msg["role"], avatar=":material/person:" if msg["role"] == "user"
                             else ":material/smart_toy:"):  # fmt: skip
            st.markdown(msg["text"])
            for j, action in enumerate(msg["actions"]):
                a = Action(**action)
                if st.button(f"{a.label} →", key=f"act_{i}_{j}"):
                    open_page(action)


conversation()
picked = None
cols = st.columns(len(STARTERS[ctx.role]))
for col, command in zip(cols, STARTERS[ctx.role], strict=True):
    if col.button(STARTER_WORDS[command], key=f"starter_{command}", width="stretch"):
        picked = command
typed = st.chat_input("Ask, or type /help for the actions")
if picked or typed:
    ask(picked or typed)
    st.rerun()
