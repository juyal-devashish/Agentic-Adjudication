"""Assignments (Flows 3 and 4, P-44, P-51): the CEC Coordinator's overview.

Who reviews which case, and whether each review is signed (not what anyone answered).
The Coordinator can move an open review to another eligible adjudicator, with a reason,
and start a re-adjudication of a closed case.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
import theme
from common import case_header, context, paths, pipeline, users, your_tasks

from adjud.flow3 import Flow3, Flow3Refused, current_round
from adjud.flow4 import Flow4, Flow4Refused
from adjud.signatures import SignatureRefused
from adjud.workflow import State

ctx = context()
store = pipeline().store
flow3, flow4 = Flow3(store, paths().config), Flow4(store, paths().config)
names = {u["id"]: u["name"] for u in users()}

if ctx.role != "cec_coordinator":
    st.title("Assignments")
    st.write("This page is for the CEC Coordinator.")
    st.stop()


def holder(rnd, slot: str) -> str:
    if slot not in rnd.holders:
        return ""
    signed = " (signed)" if slot in rnd.signed else ""
    return names.get(rnd.holders[slot], rnd.holders[slot]) + signed


if ctx.case_id is None:
    st.title("Assignments")
    your_tasks(ctx, "assignments")
    rows = []
    for cs in store.list_case_states():
        rnd = current_round(store, cs.case_id)
        if rnd is None:
            continue
        rows.append(
            {
                "Case": cs.case_id,
                "Status": theme.state_words(cs.state),
                "Round": rnd.number,
                "Adjudicator 1": holder(rnd, "1"),
                "Adjudicator 2": holder(rnd, "2"),
                "Adjudicator 3": holder(rnd, "3"),
            }
        )
    if not rows:
        st.write(
            "No case is in adjudication yet. Reviews are assigned automatically, by "
            "round-robin, as soon as a case is ready for adjudication."
        )
    else:
        st.write(
            "Reviews are assigned automatically, by round-robin, never to an adjudicator "
            "from the case's site. Select a case in the sidebar to reassign a review."
        )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.stop()

cid = ctx.case_id
your_tasks(ctx, "assignments")
case_header(ctx, "views/assignments.py")
rnd = current_round(store, cid)
state = store.state(cid).state
if rnd is None:
    st.write("No review has been assigned on this case yet.")
    st.stop()

st.header(f"Round {rnd.number}")
st.dataframe(
    pd.DataFrame(
        [
            {"Review": f"Adjudicator {s}", "Who": holder(rnd, s)}
            for s in ("1", "2", "3")
            if s in rnd.holders
        ]
    ),
    hide_index=True,
    width="stretch",
)

open_slots = [
    s
    for s in (
        ("1", "2") if state == State.ADJ_REVIEW else ("3",) if state == State.ADJ3_REVIEW else ()
    )
    if s not in rnd.signed
]
free = [u for u in flow3.pool(cid) if u not in rnd.holders.values()]
# What the form showed: if it changed before the click (a signature, another tab), the
# rebuilt boxes may hold another review or person than the one chosen, so refuse.
shown = (state, rnd.number, rnd.passes, dict(rnd.holders), sorted(rnd.signed), free)
seen = st.session_state.get(f"reassign_shown_{cid}")
st.session_state[f"reassign_shown_{cid}"] = shown
if open_slots:
    st.subheader("Reassign an open review")
    with st.form(f"reassign_{cid}", clear_on_submit=True):
        slot = st.selectbox("Review", open_slots, format_func=lambda s: f"Adjudicator {s}")
        to = st.selectbox("To", free, format_func=names.get)
        reason = st.text_input("Reason (required)")
        if st.form_submit_button("Reassign", type="primary", disabled=not free):
            try:
                if seen != shown:
                    raise Flow3Refused("the assignments changed while the page was open; "
                                       "check them and choose again")  # fmt: skip
                flow3.reassign(cid, ctx.actor, slot, to, reason, expected_from=rnd.holders[slot])
                st.toast("Review reassigned")
                st.rerun()
            except Flow3Refused as exc:
                st.error(f"Not reassigned: {exc}.")
    theme.quiet(
        "The new adjudicator starts from an empty form: an unsigned draft is never passed on."
    )

if state == State.CLOSED:
    st.subheader("Re-adjudicate")
    st.write(
        "Starts round " + str(rnd.number + 1) + " with the same adjudicators. Nothing from "
        "earlier rounds changes; the current outcome becomes superseded, and the new export "
        "is marked as a correction."
    )
    with st.form(f"readjudicate_{cid}"):
        reason = st.text_input("Reason (required)")
        password = st.text_input(
            f"Password for {ctx.user['name']} (demo password: demo)", type="password"
        )
        if st.form_submit_button("Sign and start re-adjudication", type="primary"):
            try:
                flow4.start_readjudication(cid, ctx.actor, password, reason)
                st.toast("Re-adjudication started")
                st.rerun()
            except SignatureRefused as exc:
                msg = "the password is wrong" if str(exc) == "wrong password" else str(exc)
                st.error(f"Not started: {msg}.")
            except Flow4Refused as exc:
                st.error(f"Not started: {exc}.")
