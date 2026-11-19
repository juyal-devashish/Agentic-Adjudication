"""Quality check (Flow 2, P-37, P-39): the Safety Specialist's one review stop.

Before the Safety Specialist looks, code has run the checks, the AI has prepared its
Suggestion, the dossier is built and the narrative drafted. Here the Safety Specialist
reviews it all and signs once: the pass together with the narrative as edited, or a
Site Query. The AI supports the review; it never decides (ADR 0001).
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st
import theme
from common import case_header, case_list, context, paths, pipeline, your_tasks
from preview import show_file

from adjud.dossier import latest_dossier
from adjud.flow2 import QC_QUERY_CODES, QUERY_CODES, Flow2, Flow2Refused, qc_drafts
from adjud.handoff import latest_manifest_ref
from adjud.signatures import SignatureRefused
from adjud.workflow import State

ctx = context()
store = pipeline().store
flow2 = Flow2(store, paths().config)

if ctx.case_id is None:
    st.title("Quality check")
    your_tasks(ctx, "qc")
    case_list(ctx, "Select a case to see its quality check and narrative.")
    st.stop()

cid = ctx.case_id
your_tasks(ctx, "qc")
case_header(ctx, "views/quality_check.py")
state = store.state(cid).state
events = store.events(cid)
is_ss = ctx.role == "safety_specialist"


def when(e: dict) -> str:
    return f"{theme.date_words(e['at'])} {e['at'][11:16]} UTC"


FIELD_RESULT = {
    "match": ("Matches", "good"),
    "mismatch": ("Doesn't match", "bad"),
    "not_found": ("Not in the documents", "none"),
    "not_sure": ("Not sure", "wait"),
}
DOC_RESULT = {"ok": ("Complete", "good"), "problem": ("Missing", "bad"),
              "not_sure": ("Not sure", "wait")}  # fmt: skip


def quotes(evidence: list[dict]) -> str:
    return " ".join(f"{c['file']} p. {c['page']}: “{c['quote']}”" for c in evidence)


def show_suggestion(sg: dict) -> None:
    """The AI Suggestion (P-39): the AI's findings and its reasons, never a decision."""
    st.subheader("AI Suggestion")
    if sg.get("status") != "ok":
        st.write(f"No AI Suggestion this round ({sg.get('note') or 'AI is off'}). Review the "
                 "checks and the documents yourself.")  # fmt: skip
    else:
        theme.quiet(f"Prepared by {sg['label']}. Code checked every quote against its page; "
                    "a finding it could not verify, or one the AI was unsure of, shows as "
                    "Not sure.")  # fmt: skip
        if sg.get("summary"):
            st.write(sg["summary"])
    docs = sg.get("documents")
    if docs:
        word, ink = DOC_RESULT[docs["status"]]
        st.html(
            f"<p><b>Documents</b>: {theme.status(word, ink)}. Required for this event: "
            f"{html.escape(', '.join(docs['required']))}."
            + (f" {html.escape(docs['detail'])}." if docs.get("detail") else "")
            + "</p>"
        )
        if docs.get("would_help"):
            items = "".join(f"<li>{html.escape(d['label'])}: {html.escape(d['reason'])}</li>"
                            for d in docs["would_help"])  # fmt: skip
            st.html(f"<p>Would also help (advice, not required):</p><ul>{items}</ul>")
    if sg.get("fields"):
        st.html("<p><b>Match with the EDC</b></p>" + match_table(sg["fields"]))


def match_table(fields: list[dict]) -> str:
    """Field by field: the EDC value (from the snapshot), what the documents say, the
    result and the AI's reason with the quotes code verified, or why it is not sure."""
    rows = []
    for f in fields:
        word, ink = FIELD_RESULT[f["status"]]
        cites = "".join(
            f"<br>{html.escape(c['file'])} p. {c['page']}: <q>{html.escape(c['quote'])}</q>"
            for c in f["evidence"]
        )
        problems = (f"<br>Not used: {html.escape('; '.join(f['problems']))}"
                    if f["problems"] else "")  # fmt: skip
        rows.append(  # data-label: the column name, shown beside each cell on a phone
            f"<tr><td data-label='Field'><b>{html.escape(f['label'])}</b></td>"
            f"<td data-label='EDC'>{html.escape(f['edc_value'] or '(empty)')}</td>"
            f"<td data-label='Documents'>{html.escape(f['document_value'] or '')}</td>"
            f"<td data-label='Result'>{theme.status(word, ink)}</td>"
            f"<td data-label='Why' class='why'>{html.escape(f['reason'])}{cites}{problems}</td>"
            "</tr>"
        )
    head = "".join(f"<th>{h}</th>" for h in ("Field", "EDC", "Documents", "Result", "Why"))
    return (f"<table class='adj-match'><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")  # fmt: skip


def show_agent_answers(latest: dict) -> None:
    proposals = [a for a in latest.get("ai", []) if a["status"] != "unavailable"]
    if not proposals:
        return
    st.subheader("What the AI proposed")
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


def draft_note(nar: dict) -> str:
    if nar["source"] == "template":
        why = f" ({'; '.join(nar['reasons'])})" if nar.get("reasons") else ""
        return f"Drafted from the study template{why}. Complete it from the dossier."
    label = nar.get("label") or ("Simulated AI" if nar.get("simulated") else "AI")
    if nar.get("reasons"):
        return f"Drafted by {label}. Code found problems: {'; '.join(nar['reasons'])}."
    return (f"Drafted by {label}. Every quote was found on the dossier page it cites. Check "
            "the facts against the dossier and edit if needed.")  # fmt: skip


def start_note(nar: dict, drafts, rnd: int) -> str:
    """Where the narrative below comes from: the draft of this round, or what carries
    over (P-58 drafts from Slack, P-59 edits in the app and the earlier AI draft)."""
    at = drafts.narrative_at
    when_ = f"{theme.date_words(at)} {at[11:16]} UTC" if at else ""
    if drafts.narrative_via == "earlier_ai":
        return ("This round's AI draft could not be written for the new documents, so the "
                f"narrative starts from the AI draft of round {drafts.narrative_round}, before "
                "the query to the site. Its dossier page references are to that round's "
                "dossier: check them, and add what the new documents show.")  # fmt: skip
    if drafts.narrative is not None:
        where = " from Slack" if drafts.narrative_via in ("slack", None) else ""
        if (drafts.narrative_round or 0) < rnd:  # carried over a query (QA, 2026-11-19)
            return (f"The narrative starts from your unsigned draft{where} of round "
                    f"{drafts.narrative_round}, before the query to the site, {when_}. Its "
                    "dossier page references are to that round's dossier: check them, and "
                    "add what the new documents show.")  # fmt: skip
        return f"The narrative starts from your unsigned draft{where}, {when_}."
    return draft_note(nar)


def review_and_sign(latest: dict) -> None:
    """One stop, one signature (P-39): pass with the narrative as edited, or a Site Query."""
    nar = latest["narrative"]
    draft = store.get_file(cid, nar["draft"]["path"]).decode()
    # Unsigned drafts (from Slack, P-58, or this page, P-59) pre-fill the narrative and
    # the query; the latest of them chooses the decision.
    drafts = qc_drafts(store, cid)
    start = drafts.narrative or draft
    st.header("Narrative")
    st.write(start_note(nar, drafts, latest["round"]))
    st.caption("Every statement must come from the dossier. No opinion on cause, no "
               "adjudication wording, nothing that reveals allocation.")  # fmt: skip
    if not is_ss:
        theme.frozen_text(start, "Narrative draft")
        st.write("Waiting for the Safety Specialist to decide.")
        return
    # The narrative box stays on the page whatever the decision (Nirav's review, P-59): a
    # box taken away while "query" was chosen came back empty. Each edit is also saved as
    # an unsigned draft, so it survives a refresh, a new tab from Slack, and a query to
    # the site: the next round starts from it.
    box = f"narrative_{cid}_{latest['round']}"

    def _save() -> None:
        new = st.session_state[box]
        if new.strip() and new.strip() != start.strip():
            try:
                flow2.save_narrative_draft(cid, ctx.actor, new, via="app")
            except Flow2Refused as exc:
                st.toast(f"Not saved: {exc}.")

    if box not in st.session_state:
        st.session_state[box] = start
    text = st.text_area("Narrative", height=320, key=box, on_change=_save)
    theme.quiet(
        "Your edits are saved, unsigned, each time you click outside the box, and "
        "kept if you send a query."
    )
    st.header("Your decision")
    blocked = latest.get("failed_by_code") or []
    options = ["query"] if blocked else ["pass", "query"]
    if blocked:
        st.caption("A check failed, so this round can only end in a query to the site.")
    suggested_query = latest.get("proposal") == "query"
    drafted_query = drafts.latest == "query"
    if drafts.query_at:
        at = drafts.query_at
        theme.quiet(f"The Site Query below starts from your unsigned draft from Slack, "
                    f"{theme.date_words(at)} {at[11:16]} UTC.")  # fmt: skip
    # Nothing is pre-selected when the Suggested Outcome is "unclear" (AI unsure or off).
    if suggested_query or blocked or drafted_query:
        preselect = options.index("query")
    else:
        preselect = 0 if latest.get("proposal") == "pass" else None
    # The decision sits outside the form so the fields for it appear as soon as it is
    # chosen (widgets inside a form do not redraw the page until submitted).
    decision = st.radio(
        "Your decision",
        options,
        index=preselect,
        format_func={
            "pass": "Pass: the documents are complete and fit for adjudication; sign the "
            "narrative above with it",
            "query": "Send a query to the site",
        }.get,
        key=f"qc_decision_{cid}_{latest['round']}",
        label_visibility="collapsed",
    )
    if decision is None:
        st.write("Choose pass or query to sign.")
        return
    # Plain widgets and a button, not a form (QA, 2026-11-19): leaving the narrative box
    # reruns the page, and a form's submit click arriving with that rerun was dropped, so
    # the first click on Sign after an edit did nothing.
    k = f"{cid}_{latest['round']}"
    with st.container(border=True):
        codes, message, reason = [], "", ""
        if decision == "query":
            codes = st.multiselect("Query reasons", QC_QUERY_CODES,
                                   default=drafts.query_codes or latest.get("query_codes") or [],
                                   format_func=QUERY_CODES.get, key=f"qc_codes_{k}")  # fmt: skip
            suggested_text = (" ".join(f"{r.rstrip('.')}." for r in latest.get("reasons", []))
                              if suggested_query else "")  # fmt: skip
            message = st.text_area("Message to the site", drafts.query_text or suggested_text,
                                   key=f"qc_message_{k}")  # fmt: skip
        else:
            reason = st.text_input(
                "Why you pass despite the suggested query (required)"
                if suggested_query
                else "Note for the audit trail (optional)",
                key=f"qc_reason_{k}",
            )
        password = st.text_input(
            f"Password for {ctx.user['name']} (demo password: demo)",
            type="password",
            key=f"qc_password_{k}",
        )
        label = "Sign the pass and the narrative" if decision == "pass" else "Sign the query"
        if st.button(label, type="primary", key=f"qc_sign_{k}"):
            try:
                if decision == "pass":
                    flow2.sign_qc_pass(cid, ctx.actor, password, text, reason)
                    st.toast("Quality check passed and narrative signed")
                else:
                    flow2.open_query(cid, codes, message, user_id=ctx.actor, password=password)
                    st.toast("Query sent to the site")
                st.rerun()
            except SignatureRefused as exc:
                msg = "the password is wrong" if str(exc) == "wrong password" else str(exc)
                st.error(f"Not signed: {msg}. Try again.")
            except Flow2Refused as exc:
                st.error(f"Not signed: {exc}.")


# --- quality check ----------------------------------------------------------------------------
st.header("Quality check")
rounds = [e for e in events if e["type"] == "qc.checks_completed"]
if not rounds:
    st.write("The quality check starts automatically once the site has signed and submitted.")
else:
    latest = store.get_json(cid, rounds[-1]["data"]["results"]["path"])
    st.write(f"Round {latest['round']}, {when(rounds[-1])}.")
    if latest["checks"]:
        checks = pd.DataFrame(
            [
                {
                    "Check": c["label"],
                    "Result": {"passed": "Passed", "failed": "Failed"}.get(
                        c.get("status", ""), "Passed" if c["passed"] else "Not sure"
                    ),
                    "Details": c.get("detail", ""),
                }
                for c in latest["checks"]
            ]
        )
        failed = int((checks["Result"] == "Failed").sum())
        unsure = int((checks["Result"] == "Not sure").sum())
        summary = [theme.status(f"{failed} failed", "bad")] if failed else []
        summary += [theme.status(f"{unsure} not sure", "wait")] if unsure else []
        st.html(
            "<p>"
            + (
                f"{len(checks)} checks: " + ", ".join(summary)
                if summary
                else theme.status(f"All {len(checks)} checks passed", "good")
            )
            + "</p>"
        )
        st.dataframe(
            checks.style.map(
                lambda v: (
                    "color: "
                    f"{theme.STATUS_INK[{'Passed': 'good', 'Failed': 'bad'}.get(v, 'wait')]}; "
                    "font-weight: 600"
                ),
                subset=["Result"],
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Check": st.column_config.TextColumn(width=400),
                "Result": st.column_config.TextColumn(width=80),
                "Details": st.column_config.TextColumn(width="large"),
            },
        )
    suggestion = latest.get("suggestion")
    if suggestion is not None:
        show_suggestion(suggestion)
    else:  # rounds from before P-39: the agents' answers to what code could not tell
        show_agent_answers(latest)
    # The checks and the AI suggest; the Safety Specialist decides every case (P-37, ADR
    # 0001). Rounds from before P-37 carry a decision instead of a proposal.
    proposal = latest.get("proposal")
    if proposal:
        word, ink = {
            "pass": ("pass the quality check", "good"),
            "query": ("send a query to the site", "bad"),
            "unclear": ("none, something is not sure", "wait"),
        }[proposal]
        st.html(f"<p><b>Suggested outcome</b>: {theme.status(word, ink)}</p>")
        theme.quiet("Worked out by code from the checks and the AI Suggestion. You decide.")
    if latest.get("reasons"):
        items = "".join(f"<li>{html.escape(r)}</li>" for r in latest["reasons"])
        st.html(f"<ul>{items}</ul>")
    ref = latest_manifest_ref(store, cid)
    documents = store.get_json(cid, ref["path"])["documents"] if ref else []
    if documents:
        with st.expander("Documents as submitted"):
            names = [d["source_name"] for d in documents]
            pick = st.selectbox("Document", names, key=f"qc_doc_{cid}")
            doc = documents[names.index(pick)]
            show_file(store.get_file(cid, doc["path"]), doc["path"], pick, f"qc_page_{cid}_{pick}")
    for e in [e for e in events if e["type"] == "qc.signed_pass" and e["at"] >= rounds[-1]["at"]]:
        note = f" Note: {e['reason']}" if e.get("reason") else ""
        st.success(f"Passed and signed by {e['signature']['signer']}, {when(e)}.{note}")


def by_whom(e: dict) -> str:
    if e["actor"] == "system:cec":  # from a signed more-data request (P-47)
        return "the adjudication (a signed request for more data)"
    if e["actor"].startswith("system:"):
        return "the automatic check"
    if e["actor"].startswith("agent:"):
        return "the AI agent, after code checked its answer"
    return e["signature"]["signer"] if e.get("signature") else e["actor"]


for e in [e for e in events if e["type"] == "query.opened"]:
    d = e["data"]
    by = by_whom(e)
    reasons = ", ".join(QUERY_CODES[c].lower() for c in d["codes"])
    # Answered once the site submitted again after it (QA, 2026-11-19: it looked open)
    answered = next((x for x in events if x["type"] == "submission.signed"
                     and x["seq"] > e["seq"]), None)  # fmt: skip
    status = f" Answered by the site, {when(answered)}." if answered else ""
    st.info(f"Query sent {when(e)} by {by}: {reasons}. {d['text']}{status}")

# --- dossier ---------------------------------------------------------------------------------
dossier = latest_dossier(store, cid)
if dossier:
    st.header("Dossier")
    pages = "; ".join(f"{d['name']} from page {d['first_page']}" for d in dossier["page_map"])
    st.write(
        f"Version {dossier['version']}, built for this quality check: cover, EDC summary, "
        f"then {pages}. Every page carries the subject ID and case number. A signed pass "
        "fixes this version for the adjudicators."
    )
    st.download_button(
        "Download the dossier (PDF)",
        store.get_file(cid, dossier["dossier"]["path"]),
        file_name=f"{cid}_dossier_v{dossier['version']}.pdf",
        mime="application/pdf",
    )
    with st.expander("Technical details"):
        st.write(f"SHA-256 {dossier['dossier']['sha256'].removeprefix('sha256:')}")

# --- the one review stop (P-39) ---------------------------------------------------------------
if state == State.QC_REVIEW and latest.get("narrative"):
    review_and_sign(latest)
    st.stop()

# --- narrative --------------------------------------------------------------------------------
st.header("Narrative")
signed = [
    e
    for e in events
    if e["type"] in ("narrative.signed", "narrative.approved", "quality_check.signed")
]
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
    if e["type"] == "quality_check.signed":
        st.success(f"Quality check passed and narrative signed by {by}, {when(e)}."
                   + (f" Note: {e['reason']}" if e.get("reason") else ""))  # fmt: skip
        if e["data"].get("edited"):
            theme.quiet("Edited from the draft; the Case record shows what changed.")
        st.write("This version is frozen for the adjudicators.")
    else:
        st.write(f"Approved by {by}, {when(e)}. This version is frozen for the adjudicators.")
    text = store.get_file(cid, e["data"]["narrative"]["path"]).decode()
    theme.frozen_text(text, "Narrative")
elif state == State.NARRATIVE_REVIEW and drafts:
    draft = store.get_file(cid, drafts[-1]["data"]["draft"]["path"]).decode()
    source = drafts[-1]["data"]["source"]
    reasons = next((x["data"]["reasons"] for x in reversed(events)
                    if x["type"] == "narrative.review_needed"), [])  # fmt: skip
    if source == "template":
        why = f" ({'; '.join(reasons)})" if reasons else ""
        st.write(f"Drafted from the study template{why}. Complete it from the dossier.")
    else:
        label = "Simulated AI" if drafts[-1]["data"].get("simulated") else "AI"
        if reasons:
            st.write(f"Drafted by {label}. Code found problems: {'; '.join(reasons)}.")
        else:
            st.write(
                f"Drafted by {label}. Every quote was found on the dossier page it cites. "
                "Check the facts against the dossier, edit if needed, and sign."
            )
    st.caption("Every statement must come from the dossier. No opinion on cause, no "
               "adjudication wording, nothing that reveals allocation.")  # fmt: skip
    if not is_ss:
        theme.frozen_text(draft, "Draft narrative")
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
                    msg = "the password is wrong" if str(exc) == "wrong password" else str(exc)
                    st.error(f"Not signed: {msg}. Try again.")
                except Flow2Refused as exc:
                    st.error(f"Not signed: {exc}.")
elif state == State.QUERY_OPEN and drafts:
    # Kept over the query (P-59): shown as it will start the next round (QA, 2026-11-19).
    kept = (
        qc_drafts(store, cid).narrative
        or store.get_file(cid, drafts[-1]["data"]["draft"]["path"]).decode()
    )
    st.write("The site is answering the query. When the case comes back, the next round "
             "starts from this narrative:")  # fmt: skip
    theme.frozen_text(kept, "Narrative kept for the next round")
else:
    st.write("The narrative is drafted with the quality check, before the review.")
