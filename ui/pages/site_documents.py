"""Page 2 — Site documents (mvp-flow-1.md §9.2, §7.10): upload, de-identification status,
review of redactions, corrections with reasons, and Sign and submit."""

from __future__ import annotations

import io

import pandas as pd
import pymupdf
import streamlit as st
from common import context, pipeline, processor, study_config
from openpyxl import load_workbook

from adjud.documents import documents_of, upload
from adjud.review import (
    SUBMIT_MEANING,
    ReviewRefused,
    change_redactions,
    delete_document,
    submission_status,
    submit,
)
from adjud.signatures import SignatureRefused
from adjud.workflow import State, TransitionRefused

ctx = context()
pipe = pipeline()
store = pipe.store
st.title("2 · Site documents")
if ctx.case_id is None:
    st.info("Pick a case in the sidebar.")
    st.stop()

cid = ctx.case_id
cs = store.state(cid)
snap = pipe.latest_snapshot(cid)
cols = st.columns(4)
cols[0].metric("Subject (study ID)", snap["subject_id"])
cols[1].metric("Event", snap["ae_term"])
cols[2].metric("Onset", snap["onset_date"])
cols[3].metric("State", cs.state.value)
requested = study_config().get("requested_documents", [])
st.caption("Documents requested: " + ", ".join(r.replace("_", " ") for r in requested))

is_site = ctx.role == "site"
if not is_site:
    st.info("Read-only view. Switch to a **Site user** to upload, review and submit.")

docs = documents_of(store, cid)

# --- upload ---------------------------------------------------------------------------------
can_upload = is_site and cs.state in (
    State.AWAITING_DOCUMENTS,
    State.AWAITING_SITE_CONFIRMATION,
    State.DEIDENTIFYING,
)
if cs.state == State.SUBMITTED:
    st.success("Submitted and signed. The handoff to Flow 2 is on page 3.")
elif can_upload:
    with st.form("upload", clear_on_submit=True):
        files = st.file_uploader(
            "Upload documents (PDF, PNG, JPG, TIFF, XLSX; max 50 MB each)",
            accept_multiple_files=True,
        )
        label = st.selectbox("Document type", ["(not labelled)", *requested])
        if st.form_submit_button("Upload") and files:
            names = upload(
                store,
                cid,
                [(f.name, f.getvalue()) for f in files],
                ctx.actor,
                labels=None if label.startswith("(") else {f.name: label for f in files},
            )
            st.success(f"Uploaded {', '.join(names)}. De-identification starts shortly.")
            st.rerun()

if store.state(cid).state == State.DEIDENTIFYING:
    st.warning("De-identifying… the runner picks this up within a few seconds.")
    if st.button("Process now", type="primary"):
        with st.spinner("Detecting and removing PHI (OCR can take a few seconds per page)…"):
            summary = processor().process_case(cid)
        st.toast(f"Run {summary['run']} finished")
        st.rerun()

# --- file list ------------------------------------------------------------------------------
docs = documents_of(store, cid)
if not docs.files:
    st.write("No documents uploaded yet.")
    st.stop()

st.subheader("Files")
rows = []
for name, f in sorted(docs.files.items()):
    done = docs.processed.get(name)
    status = done["status"] if done else "processing"
    rows.append(
        {
            "file": name,
            "type": f.label or "",
            "status": {
                "verified": "✅ verified",
                "blocked": "⛔ blocked",
                "rejected": "❌ rejected",
                "processing": "⏳ processing",
            }.get(status, status),
            "findings redacted": done["findings"] if done else None,
            "residuals": done["residuals"] if done else None,
            "notes": "; ".join((done or {}).get("warnings", [])),
        }
    )
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
for name in sorted(docs.files):
    done = docs.processed.get(name)
    if done and done["status"] == "rejected":
        report = store.get_json(cid, done["report"]["path"])
        st.error(f"**{name}** was rejected: {report['message']}")

# --- review -----------------------------------------------------------------------------------
reviewable = [
    n for n in sorted(docs.files) if docs.processed.get(n) and docs.processed[n].get("output")
]
if reviewable:
    st.subheader("Review")
    name = st.selectbox("File", reviewable)
    done = docs.processed[name]
    report = store.get_json(cid, done["report"]["path"])
    output = store.get_file(cid, done["output"]["path"])
    left, right = st.columns([3, 2])
    with left:
        if done["output"]["path"].endswith(".xlsx"):
            wb = load_workbook(io.BytesIO(output), data_only=True)
            for ws in wb.worksheets:
                st.caption(
                    f"Sheet **{ws.title}**" + (" (hidden)" if ws.sheet_state != "visible" else "")
                )
                st.dataframe(pd.DataFrame(ws.values), hide_index=True, width="stretch")
        else:
            with pymupdf.open(stream=output, filetype="pdf") as doc:
                n = st.number_input("Page", 1, doc.page_count, 1) if doc.page_count > 1 else 1
                png = doc[n - 1].get_pixmap(dpi=90).tobytes("png")
            st.image(png, caption=f"Redacted output, page {n}", width="stretch")
    with right:
        if report["residuals"]:
            st.error(
                "Verification still finds possible identifiers in the output. Add the "
                "missing term below, or replace the file:\n\n"
                + "\n".join(
                    f"- {r['source']}: {r['category']} `{r['masked']}`" for r in report["residuals"]
                )
            )
        findings = {}
        for f in report["findings"]:
            findings.setdefault(f["id"], {**f, "count": 0})["count"] += 1
        table = pd.DataFrame(
            [
                {
                    "keep (un-redact)": False,
                    "where": f["source"],
                    "category": f["category"],
                    "found by": f["detector"],
                    "text": f["masked"],
                    "times": f["count"],
                    "id": f["id"],
                }
                for f in findings.values()
            ]
        )
        st.caption(
            f"{len(findings)} distinct findings redacted. Untick (keep) only text that "
            "is not an identifier, e.g. a drug name flagged as a person."
        )
        edited = (
            st.data_editor(
                table,
                hide_index=True,
                width="stretch",
                disabled=not is_site,
                column_config={"id": None},
                key=f"findings_{name}_{done['report']['path']}",
            )
            if len(table)
            else table
        )
        can_change = is_site and store.state(cid).state == State.AWAITING_SITE_CONFIRMATION
        reason = st.text_input("Reason for un-redacting", disabled=not can_change)
        if st.button("Apply corrections", disabled=not can_change):
            keep = [
                {"file": name, "id": r["id"], "masked": r["text"], "category": r["category"]}
                for _, r in edited.iterrows()
                if r["keep (un-redact)"]
            ]
            try:
                change_redactions(store, cid, ctx.actor, reason=reason, keep=keep)
                st.rerun()
            except (ReviewRefused, TransitionRefused) as exc:
                st.error(str(exc))
        term = st.text_input(
            "Add a term to redact on all pages of all files", disabled=not can_change
        )
        if st.button("Redact this term", disabled=not (can_change and term.strip())):
            change_redactions(store, cid, ctx.actor, add_terms=[term])
            st.rerun()
        if is_site and st.button(f"Delete {name}", disabled=cs.state == State.SUBMITTED):
            delete_document(store, cid, name, ctx.actor, reason="deleted by site")
            st.rerun()

for name in sorted(docs.files):  # rejected files can only be deleted
    done = docs.processed.get(name)
    if is_site and done and done["status"] == "rejected" and cs.state != State.SUBMITTED:
        if st.button(f"Delete rejected file {name}"):
            delete_document(store, cid, name, ctx.actor, reason="rejected file removed")
            st.rerun()

# --- sign and submit --------------------------------------------------------------------------
st.subheader("Sign and submit")
status = submission_status(store, cid)
if store.state(cid).state == State.SUBMITTED:
    st.success("Already submitted.")
elif not status.ready:
    st.button("Sign and submit", disabled=True)
    st.caption("Not yet possible:\n" + "\n".join(f"- {r}" for r in status.reasons))
elif not is_site:
    st.info("Ready for the site to sign.")
else:
    st.write(f"You are signing: **{SUBMIT_MEANING}** — {len(status.records)} file(s).")
    with st.form("sign"):
        password = st.text_input(f"Password for {ctx.user['name']} (demo: `demo`)", type="password")
        if st.form_submit_button("Sign and submit", type="primary"):
            try:
                submit(store, cid, ctx.actor, password, pipe.paths.config)
                st.success("Submitted. The handoff package for Flow 2 is being written.")
                st.rerun()
            except (SignatureRefused, ReviewRefused) as exc:
                st.error(f"Not submitted: {exc}")
