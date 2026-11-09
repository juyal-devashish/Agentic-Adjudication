"""Quality check (Flow 2, plan steps 13–17): the Safety Specialist's exception desk.

The runner and its agents do the routine work; this page shows what they found and lets
the Safety Specialist decide the cases they could not: pass the quality check, send a
query to the site, and complete and sign the narrative.
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st
import theme
from common import case_header, context, paths, pipeline

from adjud.flow2 import QUERY_CODES, Flow2, Flow2Refused
from adjud.signatures import SignatureRefused
from adjud.workflow import State

ctx = context()
store = pipeline().store
flow2 = Flow2(store, paths().config)

if ctx.case_id is None:
    st.title("Quality check")
    st.write("Choose a case in the sidebar to see its quality check and narrative.")
    st.stop()

cid = ctx.case_id
case_header(ctx)
state = store.state(cid).state
events = store.events(cid)
is_ss = ctx.role == "safety_specialist"


def when(e: dict) -> str:
    return f"{theme.date_words(e['at'])} {e['at'][11:16]} UTC"


# --- quality check ----------------------------------------------------------------------------
st.header("Quality check")
rounds = [e for e in events if e["type"] == "qc.checks_completed"]
if not rounds:
    st.write("The quality check starts automatically once the site has signed and submitted.")
else:
    latest = store.get_json(cid, rounds[-1]["data"]["results"]["path"])
    st.write(f"Round {latest['round']}, {when(rounds[-1])}.")
    if latest["checks"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Check": c["label"],
                        "Result": "Passed" if c["passed"] else "Failed",
                        "Details": c.get("detail", ""),
                    }
                    for c in latest["checks"]
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    proposals = [a for a in latest.get("ai", []) if a["status"] != "unavailable"]
    if proposals:
        st.subheader("What the AI proposed")
        st.write(
            "Agents answer only what the checks could not. Code accepts an answer only if "
            "it is confident enough and every quote is found on the page it names."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Question": f"{a['task']} of {a['about']}",
                        "Source": a["label"],
                        "Answer": a["answer"] or a["note"],
                        "Accepted": "Yes"
                        if a["gate"] and a["gate"]["passed"]
                        else "No: " + "; ".join((a["gate"] or {}).get("reasons", [a["note"]])),
                    }
                    for a in proposals
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    if latest.get("reasons"):
        items = "".join(f"<li>{html.escape(r)}</li>" for r in latest["reasons"])
        st.html(f"<p>Why a person is needed:</p><ul>{items}</ul>")

for e in [e for e in events if e["type"] == "query.opened"]:
    d = e["data"]
    by = "the automatic check" if e["actor"].startswith("system:") else e["actor"]
    reasons = ", ".join(QUERY_CODES[c].lower() for c in d["codes"])
    st.info(f"Query sent {when(e)} by {by}: {reasons}. {d['text']}")

if state == State.QC_REVIEW:
    if not is_ss:
        st.write("Waiting for the Safety Specialist to decide.")
    else:
        with st.form("qc_decision"):
            decision = st.radio(
                "Your decision",
                ["pass", "query"],
                format_func={
                    "pass": "The documents are complete and fit for adjudication",
                    "query": "Send a query to the site",
                }.get,
            )
            codes = st.multiselect(
                "Query reasons (for a query)", list(QUERY_CODES), format_func=QUERY_CODES.get
            )
            text = st.text_area("Message to the site (for a query)")
            reason = st.text_input("Note for the audit trail (optional for a pass)")
            password = st.text_input(
                f"Password for {ctx.user['name']} (demo password: demo)", type="password"
            )
            if st.form_submit_button("Sign decision", type="primary"):
                try:
                    if decision == "pass":
                        flow2.sign_qc_pass(cid, ctx.actor, password, reason)
                        st.toast("Quality check passed and signed")
                    else:
                        flow2.open_query(cid, codes, text, user_id=ctx.actor, password=password)
                        st.toast("Query sent to the site")
                    st.rerun()
                except SignatureRefused as exc:
                    msg = "the password is wrong" if "password" in str(exc) else str(exc)
                    st.error(f"Not signed: {msg}. Try again.")
                except Flow2Refused as exc:
                    st.error(f"Not signed: {exc}.")

# --- narrative --------------------------------------------------------------------------------
st.header("Narrative")
signed = [e for e in events if e["type"] in ("narrative.signed", "narrative.approved")]
drafts = [e for e in events if e["type"] == "narrative.drafted"]
if signed:
    e = signed[-1]
    by = e["signature"]["signer"] if e.get("signature") else "the narrative check"
    st.write(f"Approved by {by}, {when(e)}. This version is frozen for the adjudicators.")
    text = store.get_file(cid, e["data"]["narrative"]["path"]).decode()
    st.text_area("Narrative", text, height=260, disabled=True)
elif state == State.NARRATIVE_REVIEW and drafts:
    draft = store.get_file(cid, drafts[-1]["data"]["draft"]["path"]).decode()
    source = drafts[-1]["data"]["source"]
    st.write(
        "Drafted from the study template."
        if source == "template"
        else "Drafted by AI. Every statement must come from a document or the EDC."
    )
    if not is_ss:
        st.text_area("Draft", draft, height=260, disabled=True)
    else:
        with st.form("narrative"):
            text = st.text_area("Narrative", draft, height=320)
            password = st.text_input(
                f"Password for {ctx.user['name']} (demo password: demo)", type="password"
            )
            if st.form_submit_button("Sign narrative", type="primary"):
                try:
                    flow2.sign_narrative(cid, ctx.actor, password, text)
                    st.toast("Narrative signed")
                    st.rerun()
                except SignatureRefused as exc:
                    msg = "the password is wrong" if "password" in str(exc) else str(exc)
                    st.error(f"Not signed: {msg}. Try again.")
                except Flow2Refused as exc:
                    st.error(f"Not signed: {exc}.")
else:
    st.write("The narrative is drafted once the quality check has passed.")
