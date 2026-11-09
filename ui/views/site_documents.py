"""Documents: upload, removal of identifiers, review of the
redacted pages, corrections with reasons, and Sign and submit."""

from __future__ import annotations

import html
import io

import pandas as pd
import pymupdf
import streamlit as st
import theme
from common import case_header, context, pipeline, processor, study_config
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

if ctx.case_id is None:
    st.title("Documents")
    st.write("Choose a case in the sidebar to upload and review its documents.")
    st.stop()

cid = ctx.case_id
case_header(ctx)
cs = store.state(cid)
requested = study_config().get("requested_documents", [])
is_site = ctx.role == "site"
if not is_site:
    st.caption(
        "You are viewing as a reviewer. Uploading, corrections and signing are done "
        "by the site user."
    )

# --- upload ---------------------------------------------------------------------------------
can_upload = is_site and cs.state in (
    State.AWAITING_DOCUMENTS,
    State.AWAITING_SITE_CONFIRMATION,
    State.DEIDENTIFYING,
)
if can_upload:
    with st.form("upload", clear_on_submit=True, border=False):
        files = st.file_uploader(
            "Add documents: PDF, scans (PNG, JPG, TIFF) or spreadsheets (XLSX), up to 50 MB each",
            accept_multiple_files=True,
        )
        label = st.selectbox(
            "What are they?",
            [None, *requested],
            format_func=lambda r: (
                "Not specified" if r is None else r.replace("_", " ").capitalize()
            ),
        )
        if st.form_submit_button("Add to case") and files:
            names = upload(
                store,
                cid,
                [(f.name, f.getvalue()) for f in files],
                ctx.actor,
                labels={f.name: label for f in files} if label else None,
            )
            st.toast(f"Added {', '.join(names)} to the case")
            st.rerun()

if store.state(cid).state == State.DEIDENTIFYING:
    st.write("The runner picks uploads up within a few seconds. To see the result now:")
    if st.button("Remove identifiers now", type="primary"):
        with st.spinner("Finding and removing identifiers. Scanned pages take a few seconds each."):
            summary = processor().process_case(cid)
        st.toast(f"Identifiers removed from {len(summary['files'])} file(s)")
        st.rerun()

# --- files ------------------------------------------------------------------------------------
docs = documents_of(store, cid)
st.header("Files")
if not docs.files:
    st.write("No documents yet." + (" Add them above." if can_upload else ""))
    st.stop()

rows = []
for name, f in sorted(docs.files.items()):
    done = docs.processed.get(name)
    word, _ = theme.FILE_STATUS[done["status"] if done else "processing"]
    rows.append(
        {
            "File": name,
            "Type": (f.label or "").replace("_", " ").capitalize(),
            "Status": word,
            "Identifiers removed": done["findings"] if done else None,
            "Still found": done["residuals"] if done else None,
        }
    )


def _ink(value: str) -> str:
    ink = next((i for w, i in theme.FILE_STATUS.values() if w == value), "none")
    return f"color: {theme.STATUS_INK[ink]}; font-weight: 600"


files_table = pd.DataFrame(rows)
if not files_table["Type"].any():  # nobody labelled the files: leave the column out
    files_table = files_table.drop(columns="Type")
st.dataframe(files_table.style.map(_ink, subset=["Status"]), hide_index=True, width="stretch")

for name in sorted(docs.files):
    done = docs.processed.get(name)
    if done and done["status"] == "rejected":
        message = store.get_json(cid, done["report"]["path"])["message"]
        st.error(f"{name} was rejected. {message}")
        if (
            is_site
            and cs.state != State.SUBMITTED
            and st.button(f"Remove {name}", key=f"rm_{name}")
        ):
            delete_document(store, cid, name, ctx.actor, reason="rejected file removed")
            st.rerun()
    elif done:
        notes = [w for w in done.get("warnings", []) if w.startswith("EXTENSION_MISMATCH")]
        if notes:
            st.caption(f"{name}: the file is an image, not a PDF. It was processed as an image.")

# --- review -----------------------------------------------------------------------------------
reviewable = [
    n for n in sorted(docs.files) if docs.processed.get(n) and docs.processed[n].get("output")
]
if reviewable:
    st.header("Review")
    name = st.selectbox("File to review", reviewable)
    done = docs.processed[name]
    report = store.get_json(cid, done["report"]["path"])
    output = store.get_file(cid, done["output"]["path"])
    can_change = is_site and store.state(cid).state == State.AWAITING_SITE_CONFIRMATION
    left, right = st.columns([3, 2], gap="large")
    with left:
        if done["output"]["path"].endswith(".xlsx"):
            wb = load_workbook(io.BytesIO(output), data_only=True)
            for ws in wb.worksheets:
                hidden = " (hidden in the original)" if ws.sheet_state != "visible" else ""
                st.caption(f"Sheet {ws.title}{hidden}")
                st.dataframe(pd.DataFrame(ws.values), hide_index=True, width="stretch")
        else:
            with pymupdf.open(stream=output, filetype="pdf") as doc:
                n = st.number_input("Page", 1, doc.page_count, 1) if doc.page_count > 1 else 1
                png = doc[n - 1].get_pixmap(dpi=90).tobytes("png")
            st.image(png, caption=f"{name}, page {n}, as it will be sent", width="stretch")
    with right:
        if report["residuals"]:
            items = "".join(
                f"<li>{html.escape(r['category'].replace('_', ' ').lower())}: "
                f"{html.escape(r['masked'])} ({html.escape(r['source'])})</li>"
                for r in report["residuals"]
            )
            st.html(
                f"<p>{theme.status('Still found after removal', 'bad')}. Add the "
                f"missing term below, or replace the file.</p><ul>{items}</ul>"
            )
        findings = {}
        for f in report["findings"]:
            findings.setdefault(f["id"], {**f, "count": 0})["count"] += 1
        st.write(
            f"{len(findings)} different identifiers were removed. Keep one only if it is "
            "not an identifier, for example a drug name taken for a person's name."
        )
        table = pd.DataFrame(
            [
                {
                    "Keep": False,
                    "Text": f["masked"],
                    "Kind": theme.category_words(f["category"]),
                    "Where": f["source"].replace("page:", "page ").replace(":", " "),
                    "id": f["id"],
                    "category": f["category"],
                }
                for f in findings.values()
            ]
        )
        edited = (
            st.data_editor(
                table,
                hide_index=True,
                width="stretch",
                disabled=not can_change,
                column_config={
                    "id": None,
                    "category": None,
                    "Keep": st.column_config.CheckboxColumn(help="Show this text again"),
                },
                key=f"findings_{done['report']['path']}",
            )
            if len(table)
            else table
        )
        reason = st.text_input("Why should the ticked text stay visible?", disabled=not can_change)
        if st.button("Keep ticked text", disabled=not can_change):
            keep = [
                {"file": name, "id": r["id"], "masked": r["Text"], "category": r["category"]}
                for _, r in edited.iterrows()
                if r["Keep"]
            ]
            try:
                change_redactions(store, cid, ctx.actor, reason=reason, keep=keep)
                st.rerun()
            except ReviewRefused as exc:
                st.error(f"Nothing changed: {exc}.")
            except TransitionRefused:
                st.error("Nothing changed: corrections are possible only during site review.")
        term = st.text_input(
            "Black out more text on every page of every file",
            disabled=not can_change,
            placeholder="For example a nickname or a local ID",
        )
        if st.button("Black out this text", disabled=not (can_change and term.strip())):
            change_redactions(store, cid, ctx.actor, add_terms=[term])
            st.rerun()
        if (
            is_site
            and cs.state != State.SUBMITTED
            and st.button(f"Remove {name}", key=f"rm_r_{name}")
        ):
            delete_document(store, cid, name, ctx.actor, reason="removed by site")
            st.rerun()

# --- sign and submit --------------------------------------------------------------------------
st.header("Sign and submit")
status = submission_status(store, cid)
if store.state(cid).state == State.SUBMITTED:
    st.write("Signed and submitted. The package for adjudication is on the case record.")
elif not status.ready:
    st.button("Sign and submit", disabled=True)
    st.write("You can sign once every file is verified:")
    st.html("<ul>" + "".join(f"<li>{html.escape(r)}</li>" for r in status.reasons) + "</ul>")
elif not is_site:
    st.write("Every file is verified. The site can now sign.")
else:
    st.write(
        f"By signing you confirm: {SUBMIT_MEANING.split('— ')[-1]}. "
        f"{len(status.records)} file(s) will be sent for adjudication."
    )
    with st.form("sign"):
        password = st.text_input(
            f"Password for {ctx.user['name']} (demo password: demo)", type="password"
        )
        if st.form_submit_button("Sign and submit", type="primary"):
            try:
                submit(store, cid, ctx.actor, password, pipe.paths.config)
                st.toast("Signed and submitted")
                st.rerun()
            except SignatureRefused as exc:
                msg = "the password is wrong" if "password" in str(exc) else str(exc)
                st.error(f"Not signed: {msg}. Try again.")
            except ReviewRefused as exc:
                st.error(f"Not signed: {exc}.")
