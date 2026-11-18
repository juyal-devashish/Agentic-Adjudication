"""Test-only entry point for the hidden Assistant page (P-56).

The app no longer registers the Assistant, so tests/test_assistant.py opens it through
this script instead: the same who-you-are as ui/app.py (role and user from session state),
the Assistant as the landing page, and every other page registered so its buttons can
switch to them. Deleted together with the Assistant (plan step 30).
"""

from __future__ import annotations

import sys
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"
if str(UI) not in sys.path:
    sys.path.insert(0, str(UI))

import assistant  # noqa: E402
import streamlit as st  # noqa: E402
from common import Context, users  # noqa: E402
from wording import PAGES  # noqa: E402

role = st.session_state.get("role", "data_manager")
user = st.session_state.get(f"user_{role}") or next(u for u in users() if u["role"] == role)
st.session_state["ctx"] = Context(user=user, case_id=st.session_state.get("case_id"))

chat = st.Page(str(UI / "views" / "assistant.py"), title="Assistant",
              url_path="assistant", default=True)  # fmt: skip
pages = {key: st.Page(str(UI / file), title=title, url_path=key)
         for key, (file, title, _icon) in PAGES.items()}  # fmt: skip
# The chat's buttons switch by page key; from here the app's relative paths don't resolve,
# so they get the registered pages themselves.
assistant.PAGES = {"assistant": chat, **pages}
st.navigation([chat, *pages.values()], position="hidden").run()
