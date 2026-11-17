"""Assistant (P-40): the guided chat everyone lands on, laid out like a Teams chat.

Ask in plain words or with a slash action; the answers come from the case records (see
ui/assistant.py). While the page is open, what happens to the person's cases appears in
the chat as it happens. Every manual step is a button to the page where it is done.

The look follows the chat app the team uses every day (a header with the chat's name and
a Chat tab, initials avatars, name and time over each message, your own messages on the
right, purple action buttons and suggested replies), so the demo reads as "a chat with
an assistant". It is only a look: no other product's name or logo is used.
"""

from __future__ import annotations

import html
import io
from datetime import datetime
from functools import lru_cache

import streamlit as st
from assistant import PAGES, Action, Assistant
from common import context, organisation
from PIL import Image, ImageDraw, ImageFont

ctx = context()
helper = Assistant(ctx.user)
HISTORY = f"assistant_chat_{ctx.user['id']}"
SEEN = f"assistant_seen_{ctx.user['id']}"
BOT = "Adjudication Assistant"
STARTERS = {
    "data_manager": ["/tasks", "/import", "/why AE-008"],
    "site": ["/tasks", "/status 0001"],
    "safety_specialist": ["/tasks", "/qc 0001"],
    "cec_coordinator": ["/tasks", "/status 0001"],
    "adjudicator": ["/tasks", "/status 0001"],
}
STARTER_WORDS = {"/tasks": "What's waiting for me?", "/import": "Import the AE records",
                 "/why AE-008": "Why is AE-008 not a case?", "/status 0001": "Where is case 0001?",
                 "/qc 0001": "Summarise the quality check of 0001"}  # fmt: skip

# Teams-like chat styling, scoped to this page's keyed containers and chat messages.
CSS = """
<style>
.st-key-chat_head { border-bottom: 1px solid #E0E0E0; padding-bottom: .25rem; gap: .75rem;
  align-items: center; }
.st-key-chat_head h1 { font-size: 1.15rem !important; font-weight: 700; margin: 0;
  padding: 0 !important; }
.adj-chat-tabs { display: flex; gap: 1.25rem; font-size: .9rem; color: #616161;
  margin-top: .15rem; }
.adj-chat-tabs span.on { color: #242424; font-weight: 600; border-bottom: 3px solid #5B5FC7;
  padding-bottom: .3rem; }
.adj-chat-sub { font-size: .8rem; color: #616161; }
.st-key-chat_pane { background: #F5F5F5; border-radius: 6px; padding: .75rem 1rem 1rem;
  font-family: "Segoe UI", -apple-system, system-ui, sans-serif; }
.st-key-chat_pane [data-testid="stChatMessage"] { background: transparent; padding: .35rem 0;
  gap: .6rem; }
.st-key-chat_pane [data-testid="stChatMessageContent"] { background: #FFFFFF;
  border-radius: 6px; padding: .55rem .85rem .65rem; max-width: 46rem;
  box-shadow: 0 1px 2px rgba(0,0,0,.12); color: #242424; margin: 0 auto 0 0; }
.st-key-chat_pane [data-testid="stChatMessage"]:has(.adj-me) { flex-direction: row-reverse; }
.st-key-chat_pane [data-testid="stChatMessage"]:has(.adj-me) [data-testid="stChatMessageContent"] {
  background: #E8EBFA; margin: 0 0 0 auto; flex: 0 1 auto; }
.st-key-chat_pane [data-testid="stChatMessageContent"] p { margin-bottom: .35rem; }
.adj-who { font-size: .78rem; color: #616161; margin-bottom: .15rem; }
.adj-who b { color: #242424; font-weight: 600; margin-right: .5rem; }
.st-key-chat_pane [class*="st-key-act_"] button { border: 1px solid #5B5FC7; color: #5B5FC7;
  background: #FFFFFF; border-radius: 4px; font-weight: 600; min-height: 2rem; }
.st-key-chat_pane [class*="st-key-act_"] button:hover { background: #5B5FC7; color: #FFFFFF; }
.st-key-chat_suggest { gap: .5rem; flex-wrap: wrap; }
.st-key-chat_suggest button { border: 1px solid #5B5FC7; color: #5B5FC7; background: #FFFFFF;
  border-radius: 16px; font-size: .85rem; min-height: 1.9rem; padding: .1rem .9rem; }
.st-key-chat_suggest button:hover { background: #E8EBFA; color: #4F52B2; }
[data-testid="stChatInput"] { border-radius: 6px; border: 1px solid #D1D1D1;
  border-bottom: 2px solid #5B5FC7; background: #FFFFFF; }
@media (prefers-color-scheme: dark) {
  .st-key-chat_pane { background: #1F1F1F; }
  .st-key-chat_pane [data-testid="stChatMessageContent"] { background: #292929; color: #F0F0F0; }
  .st-key-chat_pane [data-testid="stChatMessage"]:has(.adj-me)
    [data-testid="stChatMessageContent"] { background: #3D3E66; }
  .adj-who, .adj-chat-sub, .adj-chat-tabs { color: #ADADAD; }
  .adj-who b, .adj-chat-tabs span.on { color: #F0F0F0; }
}
</style>
"""


@lru_cache(maxsize=32)
def avatar(initials: str, colour: str) -> bytes:
    """A round initials avatar, as Teams draws people without a photo."""
    size = 96
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size - 1, size - 1), fill=colour)
    font = ImageFont.load_default(size=40)
    draw.text((size / 2, size / 2), initials, fill="white", font=font, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def initials(name: str) -> str:
    words = [w for w in name.replace("Dr.", "").split() if w[:1].isalpha()]
    return (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper()


BOT_AVATAR = avatar("AA", "#5B5FC7")
ME_AVATAR = avatar(initials(ctx.user["name"]), "#C4314B" if ctx.role == "site" else "#0F6CBD")


def now() -> str:
    return datetime.now().strftime("%H:%M")


def stamp(msg: dict) -> dict:
    return {**msg, "at": msg.get("at") or now()}


if HISTORY not in st.session_state:
    st.session_state[HISTORY] = [stamp(helper.greeting().as_dict())]
    st.session_state[SEEN] = {cs.case_id: cs.seq for cs in helper.cases()}

st.html(CSS)
with st.container(key="chat_head", horizontal=True):
    st.image(BOT_AVATAR, width=40)
    with st.container():
        st.title("Assistant")
        st.html(
            '<div class="adj-chat-tabs"><span class="on">Chat</span></div>'
            f'<div class="adj-chat-sub">{html.escape(BOT)} · {html.escape(organisation())}. '
            "Answers come from the case records; where the AI wrote something, it says so. "
            "You sign and decide on the pages the buttons open.</div>"
        )


def ask(text: str, shown: str | None = None) -> None:
    """`shown`: the words on a suggested reply, posted as the person's message."""
    st.session_state[HISTORY].append({"role": "user", "text": shown or text, "actions": [],
                                      "at": now()})  # fmt: skip
    st.session_state[HISTORY].append(stamp(helper.handle(text).as_dict()))
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
        st.session_state[HISTORY].append(stamp(reply.as_dict()))
    with st.container(key="chat_pane"):
        for i, msg in enumerate(st.session_state[HISTORY]):
            mine = msg["role"] == "user"
            with st.chat_message(msg["role"], avatar=ME_AVATAR if mine else BOT_AVATAR):
                who = "" if mine else f"<b>{html.escape(BOT)}</b>"
                marker = '<span class="adj-me"></span>' if mine else ""
                st.html(f'<div class="adj-who">{marker}{who}{msg.get("at", "")}</div>')
                st.markdown(msg["text"])
                for j, action in enumerate(msg["actions"]):
                    a = Action(**action)
                    if st.button(a.label, key=f"act_{i}_{j}"):
                        open_page(action)


conversation()
picked = None
with st.container(key="chat_suggest", horizontal=True):
    for command in STARTERS[ctx.role]:
        if st.button(STARTER_WORDS[command], key=f"starter_{command}"):
            picked = command
typed = st.chat_input("Type a message, or /help for the actions")
if picked or typed:
    ask(picked or typed, STARTER_WORDS.get(picked or ""))
    st.rerun()
