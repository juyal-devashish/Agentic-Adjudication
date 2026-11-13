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
from preview import show_file

from adjud.dossier import latest_dossier
from adjud.flow2 import QUERY_CODES, Flow2, Flow2Refused
from adjud.handoff import latest_manifest_ref
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
        lead = "Why a person is needed" if state == State.QC_REVIEW else "A person was needed"
        st.html(f"<p>{lead}:</p><ul>{items}</ul>")
    ref = latest_manifest_ref(store, cid)
    documents = store.get_json(cid, ref["path"])["documents"] if ref else []
    if documents:
        with st.expander("Documents as submitted", expanded=state == State.QC_REVIEW):
            names = [d["source_name"] for d in documents]
            pick = st.selectbox("Document", names, key=f"qc_doc_{cid}")
            doc = documents[names.index(pick)]
            show_file(store.get_file(cid, doc["path"]), doc["path"], pick, f"qc_page_{cid}_{pick}")
    for e in [e for e in events if e["type"] == "qc.signed_pass" and e["at"] >= rounds[-1]["at"]]:
        note = f" Note: {e['reason']}" if e.get("reason") else ""
        st.success(f"Passed and signed by {e['signature']['signer']}, {when(e)}.{note}")


def by_whom(e: dict) -> str:
    if e["actor"].startswith("system:"):
        return "the automatic check"
    if e["actor"].startswith("agent:"):
        return "the AI agent, after code checked its answer"
    return e["signature"]["signer"] if e.get("signature") else e["actor"]


for e in [e for e in events if e["type"] == "query.opened"]:
    d = e["data"]
    by = by_whom(e)
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

# --- dossier ---------------------------------------------------------------------------------
dossier = latest_dossier(store, cid)
if dossier:
    st.header("Dossier")
    pages = "; ".join(f"{d['name']} from page {d['first_page']}" for d in dossier["page_map"])
    st.write(
        f"Version {dossier['version']}, built when the quality check passed: cover, EDC "
        f"summary, then {pages}. Every page carries the subject ID and case number."
    )
    st.download_button(
        "Download the dossier (PDF)",
        store.get_file(cid, dossier["dossier"]["path"]),
        file_name=f"{cid}_dossier_v{dossier['version']}.pdf",
        mime="application/pdf",
    )
    theme.quiet(f"SHA-256 {dossier['dossier']['sha256'][:16]}…")

# --- narrative --------------------------------------------------------------------------------
st.header("Narrative")
signed = [e for e in events if e["type"] in ("narrative.signed", "narrative.approved")]
drafts = [e for e in events if e["type"] == "narrative.drafted"]
if signed:
    e = signed[-1]
    if e.get("signature"):
        by = e["signature"]["signer"]
        if e["data"].get("source") == "ai":
            by += ", from an AI draft"
    else:
        label = "Simulated AI" if e["data"].get("simulated") else "AI"
        by = (f"code, without a person: {label} wrote it and every statement was found on "
              "the dossier page it cites")  # fmt: skip
    st.write(f"Approved by {by}, {when(e)}. This version is frozen for the adjudicators.")
    text = store.get_file(cid, e["data"]["narrative"]["path"]).decode()
    st.text_area("Narrative", text, height=260, disabled=True)
elif state == State.NARRATIVE_REVIEW and drafts:
    draft = store.get_file(cid, drafts[-1]["data"]["draft"]["path"]).decode()
    source = drafts[-1]["data"]["source"]
    reasons = next((x["data"]["reasons"] for x in reversed(events)
                    if x["type"] == "narrative.review_needed"), [])  # fmt: skip
    if source == "template":
        st.write("Drafted from the study template. Complete it from the dossier.")
    else:
        label = "Simulated AI" if drafts[-1]["data"].get("simulated") else "AI"
        st.write(f"Drafted by {label}. It needs a person because: {'; '.join(reasons)}.")
    st.caption("Every statement must come from the dossier. No opinion on cause, no "
               "adjudication wording, nothing that reveals allocation.")  # fmt: skip
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
