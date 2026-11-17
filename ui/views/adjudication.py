"""Adjudication (Flow 3, P-41 to P-46): an adjudicator's own review, blind.

The workspace shows the locked dossier and the signed narrative from the Flow 3 package,
and the study's form. An adjudicator never sees another adjudicator's answers here;
Adjudicator 3 gets the disputed fields only. AI proposes nothing on the form (ADR 0001).
Drafts are saved for their author only; a signed review never changes.
"""

from __future__ import annotations

from datetime import date

import streamlit as st
import theme
from common import case_header, case_list, context, paths, pipeline

from adjud.flow3 import Flow3, Flow3Refused, current_round
from adjud.signatures import SignatureRefused
from adjud.workflow import State

ctx = context()
store = pipeline().store
flow3 = Flow3(store, paths().config)

if ctx.role != "adjudicator":
    st.title("Adjudication")
    st.write("This page is for adjudicators.")
    st.stop()
if ctx.case_id is None:
    st.title("Adjudication")
    case_list(ctx, "Your cases. Select one to open its review.")
    st.stop()

cid = ctx.case_id
case_header(ctx, "views/adjudication.py")
rnd = current_round(store, cid)
if rnd is None:
    st.write("No review has been assigned on this case yet.")
    st.stop()

# --- the workspace: what every adjudicator reviews ----------------------------------------------
package = store.get_json(cid, rnd.manifest["path"])
st.header("Dossier and narrative")
theme.quiet(
    f"Round {rnd.number}"
    + (f", review {rnd.passes} (after more data)" if rnd.passes > 1 else "")
    + f". Dossier version {package['dossier']['version']}, locked at the Quality Check."
)
dossier = package["dossier"]
st.download_button(
    "Download the dossier (PDF)",
    store.get_file(cid, dossier["path"]),
    file_name=f"{cid}_dossier_v{package['dossier']['version']}.pdf",
    mime="application/pdf",
    icon=":material/download:",
)
narrative = store.get_file(cid, package["narrative"]["path"]).decode()
theme.frozen_text(narrative, "Narrative, signed at the Quality Check")

# --- the form ----------------------------------------------------------------------------------
slot = flow3.slot_of(cid, ctx.actor)
form = flow3.form(cid)
st.header(form.title)
signed = next(
    (e for s, e in rnd.signed.items() if e["actor"] == ctx.actor and (slot is None or s == slot)),
    None,
)
if slot is None or slot in rnd.signed:
    mine = signed or next(
        (
            e
            for e in reversed(store.events(cid))
            if e["type"] in ("adjudication.signed", "adj3.signed") and e["actor"] == ctx.actor
        ),
        None,
    )
    if mine is None:
        st.write("You have no open review on this case.")
        st.stop()
    answers = store.get_json(cid, mine["data"]["answers"]["path"])
    st.write(
        f"You signed your review on {theme.date_words(mine['at'])} at {mine['at'][11:16]} UTC. "
        "It cannot be changed. The other reviews are not shown to you."
    )
    st.dataframe(
        [
            {"Question": form.field(f).label, "Your answer": form.field(f).words(v)}
            for f, v in answers.items()
        ],
        hide_index=True,
        width="stretch",
    )
    st.stop()

ids = flow3.fields_for(cid, slot)
if slot == "3":
    st.write(
        "The two independent reviews disagree on the questions below. Answer them from "
        "the dossier; the earlier answers are not shown to you (blinded)."
    )
else:
    st.write(f"You are Adjudicator {slot}. Your review is independent and blind.")
draft = flow3.draft(cid, ctx.actor)
key = f"adj_{cid}_{rnd.number}_{rnd.passes}_{slot}"
answers: dict = {}
context_values = dict(rnd.agreed) if slot == "3" else {}
for f in (form.field(i) for i in ids):
    if not f.shown({**context_values, **answers}):
        continue
    k = f"{key}_{f.id}"
    if f.type == "enum":
        opts = list(f.options)
        index = opts.index(draft[f.id]) if draft.get(f.id) in opts else None
        answers[f.id] = st.radio(f.label, opts, index=index, format_func=f.options.get, key=k)
    elif f.type == "date":
        value = date.fromisoformat(draft[f.id]) if draft.get(f.id) else None
        picked = st.date_input(f.label, value=value, key=k, format="YYYY-MM-DD")
        answers[f.id] = picked.isoformat() if picked else None
    else:
        answers[f.id] = st.text_area(f.label, draft.get(f.id, ""), key=k)

if st.button("Save draft", icon=":material/save:"):
    flow3.save_draft(cid, ctx.actor, answers)
    st.toast("Draft saved. Only you can see it.")

with st.form(f"{key}_sign"):
    password = st.text_input(
        f"Password for {ctx.user['name']} (demo password: demo)", type="password"
    )
    if st.form_submit_button("Sign my review", type="primary"):
        try:
            flow3.sign_review(cid, ctx.actor, password, answers)
            st.toast("Review signed")
            st.rerun()
        except SignatureRefused as exc:
            msg = "the password is wrong" if "password" in str(exc) else str(exc)
            st.error(f"Not signed: {msg}. Try again.")
        except Flow3Refused as exc:
            st.error(f"Not signed: {exc}.")

if slot in ("1", "2") and store.state(cid).state == State.ADJ_REVIEW:
    with st.expander("Ask for more data"):
        st.write(
            "The reviews pause and the site gets a query. The new documents go through "
            "de-identification and the Quality Check, then both reviews start again."
        )
        need = st.text_area("What is needed", key=f"{key}_more")
        if st.button("Ask the site for more data"):
            try:
                flow3.request_more_data(cid, ctx.actor, need)
                st.rerun()
            except Flow3Refused as exc:
                st.error(f"Not sent: {exc}.")
