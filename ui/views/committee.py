"""Committee (Flow 3b, P-48): the fields the reviews could not settle.

The meeting is held outside the tool (Q-14). The CEC Coordinator records it here: date,
attendees, a summary and the committee's decision on each open field; the quorum is
checked by code. The Chair signs the consensus. The committee can also ask the site for
more data; the case then comes back here (Q-06).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
import theme
from common import case_header, case_list, context, paths, pipeline, users

from adjud.flow3 import Flow3, Flow3Refused, current_round
from adjud.signatures import SignatureRefused
from adjud.workflow import State

ctx = context()
store = pipeline().store
flow3 = Flow3(store, paths().config)
names = {u["id"]: u["name"] for u in users()}
chair = flow3.chair()

if ctx.role == "adjudicator" and ctx.actor != chair:
    st.title("Committee")
    st.write(f"The committee's consensus is signed by its Chair, {names.get(chair, '')}.")
    st.stop()
if ctx.case_id is None:
    st.title("Committee")
    case_list(
        ctx, "Select a case. Cases waiting for the committee show *Waiting for the committee*."
    )
    st.stop()

cid = ctx.case_id
case_header(ctx, "views/committee.py")
state = store.state(cid).state
rnd = current_round(store, cid)
if state != State.CEC_MEETING or rnd is None:
    st.write("This case is not waiting for the committee.")
    st.stop()

form = flow3.form(cid)
fields = flow3.committee_fields(cid)
answers = {s: store.get_json(cid, e["data"]["answers"]["path"]) for s, e in rnd.signed.items()}

st.header("Open fields")
theme.quiet("The committee sees every review's answer on the fields still open.")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Question": form.field(f).label,
                **{
                    f"Adjudicator {s}": form.field(f).words(a.get(f))
                    for s, a in sorted(answers.items())
                    if f in a
                },
            }
            for f in fields
        ]
    ),
    hide_index=True,
    width="stretch",
)
notes = [(s, a.get("rationale")) for s, a in sorted(answers.items()) if a.get("rationale")]
if notes:
    with st.expander("Rationales"):
        for s, text in notes:
            st.markdown(f"**Adjudicator {s}:** {text}")

meeting = rnd.meeting
resumed = [e for e in store.events(cid) if e["type"] == "committee.resumed"]
stale = meeting is not None and resumed and resumed[-1]["seq"] > meeting["seq"]
if resumed:
    theme.quiet("More data arrived after the committee asked for it: the dossier is updated.")

if ctx.role == "cec_coordinator":
    st.header("Record the meeting")
    members = [u for u, v in flow3.users().items() if v["role"] == "adjudicator"]
    with st.form(f"meeting_{cid}"):
        held = st.date_input(
            "Meeting date", value=date.today(), max_value=date.today(), format="YYYY-MM-DD"
        )
        attendees = st.multiselect("Attendees", members, default=members, format_func=names.get)
        summary = st.text_area("Summary of the discussion")
        decisions = {}
        for f in (form.field(i) for i in fields):
            if f.type == "enum":
                decisions[f.id] = st.radio(
                    f"Decision: {f.label}",
                    f.values,
                    index=None,
                    format_func=f.label_of,
                    key=f"dec_{cid}_{f.id}",
                )
            elif f.type == "date":
                d = st.date_input(f"Decision: {f.label}", value=None, format="YYYY-MM-DD")
                decisions[f.id] = d.isoformat() if d else None
            else:
                decisions[f.id] = st.text_area(f"Decision: {f.label}")
        if st.form_submit_button("Record the meeting", type="primary"):
            try:
                flow3.record_meeting(
                    cid,
                    ctx.actor,
                    held_on=held.isoformat(),
                    attendees=attendees,
                    summary=summary,
                    decisions=decisions,
                )
                st.toast("Meeting recorded; the Chair is asked to sign")
                st.rerun()
            except Flow3Refused as exc:
                st.error(f"Not recorded: {exc}.")
    quorum = flow3.cfg.get("quorum", 3)
    theme.quiet(
        f"Quorum: at least {quorum} attendees, including the Chair ({names.get(chair, '')})."
    )
    with st.expander("Ask the site for more data"):
        need = st.text_area("What the committee needs", key=f"more_{cid}")
        pw = st.text_input(
            f"Password for {ctx.user['name']} (demo password: demo)",
            type="password",
            key=f"more_pw_{cid}",
        )
        if st.button("Sign and send the request"):
            try:
                flow3.request_more_data(cid, ctx.actor, pw, need)
                st.rerun()
            except SignatureRefused as exc:
                msg = "the password is wrong" if str(exc) == "wrong password" else str(exc)
                st.error(f"Not sent: {msg}.")
            except Flow3Refused as exc:
                st.error(f"Not sent: {exc}.")

if meeting is not None:
    record = store.get_json(cid, meeting["data"]["meeting"]["path"])
    st.header("Recorded meeting")
    who = ", ".join(names.get(a, a) for a in record["attendees"])
    quorum = theme.status("met", "good") if record["quorum_met"] else theme.status("not met", "bad")
    st.html(f"<p>{theme.date_words(record['held_on'])}. Attendees: {who}. Quorum {quorum}.</p>")
    st.write(record["summary"])
    st.dataframe(
        pd.DataFrame(
            [
                {"Question": form.field(f).label, "Decision": form.field(f).words(v)}
                for f, v in record["decisions"].items()
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    if stale:
        st.warning("This meeting was before the new data arrived. Record the next meeting.")
    elif ctx.actor == chair and record["quorum_met"]:
        # Sign the record drawn on the run the Chair saw, not one recorded since (B07).
        seen_key = f"consensus_shown_{cid}"
        shown = st.session_state.get(seen_key, meeting["data"]["meeting"])
        st.session_state[seen_key] = meeting["data"]["meeting"]
        with st.form(f"consensus_{cid}"):
            password = st.text_input(
                f"Password for {ctx.user['name']} (demo password: demo)", type="password"
            )
            if st.form_submit_button("Sign the consensus", type="primary"):
                try:
                    flow3.sign_consensus(cid, ctx.actor, password, shown=shown)
                    st.toast("Consensus signed")
                    st.rerun()
                except SignatureRefused as exc:
                    msg = "the password is wrong" if str(exc) == "wrong password" else str(exc)
                    st.error(f"Not signed: {msg}.")
                except Flow3Refused as exc:
                    st.error(f"Not signed: {exc}.")
