"""Case record: all cases, and for one case its audit trail with the
chain check, source data, notifications and the package for adjudication. Also the
de-identification measurements on the synthetic test corpus."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pandas as pd
import streamlit as st
import theme
from common import case_header, context, paths, pipeline, users

from adjud.ai.gateway import Gateway
from adjud.handoff import read_manifest
from adjud.integrity import verify_case_log

ctx = context()
pipe = pipeline()
store = pipe.store

EVENT_WORDS = {
    "case.created": "Case created",
    "edc.related_data_pulled": "Related EDC data pulled",
    "notification.queued": "Notification queued",
    "notification.sent": "Notification e-mailed",
    "notification.failed": "Notification e-mail failed",
    "ai.called": "AI asked",
    "ai.gate": "AI answer checked by code",
    "qc.started": "Quality check started",
    "qc.checks_completed": "Quality checks run",
    "qc.passed": "Quality check passed automatically",
    "qc.review_needed": "Quality check needs a person",
    "qc.signed_pass": "Quality check passed and signed",
    "query.opened": "Query sent to the site",
    "narrative.drafted": "Narrative drafted",
    "narrative.approved": "Narrative approved automatically",
    "narrative.review_needed": "Narrative needs a person",
    "narrative.signed": "Narrative signed",
    "handoff.flow3_ready": "Ready for adjudication",
    "documents.uploaded": "Documents uploaded",
    "deid.started": "Identifier removal started",
    "deid.file_processed": "File processed",
    "deid.completed": "Identifier removal finished",
    "deid.failed": "No file could be processed",
    "redactions.changed": "Site corrected the redactions",
    "document.deleted": "Document removed",
    "submission.signed": "Signed and submitted",
    "handoff.flow2_ready": "Package ready for adjudication",
    "source.changed": "AE changed in the EDC",
    "source.stale_ignored": "Older EDC record ignored",
    "case.flagged": "Case flagged",
    "case.withdrawn": "Case withdrawn",
}


def measurements() -> None:
    st.header("How well identifiers are removed")
    st.write(
        "Measured on the synthetic test kit: 17 documents with planted identifiers, scored "
        "by the kit's own harness. Scanned pages are read back by OCR, so their figures "
        "are estimates."
    )
    latest = paths().metrics / "latest.json"
    if ctx.role == "safety_specialist" and st.button("Measure again (about 30 seconds)"):
        with st.spinner("Processing the test documents and scoring them…"):
            subprocess.run(
                [sys.executable, "-m", "tools.deid_check"],
                check=False,  # noqa: S603
                capture_output=True,
            )
        st.rerun()
    if not latest.exists():
        st.write("Not measured yet. Run `uv run python -m tools.deid_check`.")
        return
    res = json.loads(latest.read_text())
    gate = res["summary"]["gate"]
    checks = {
        "text_pdf_must_redact_all_removed": "every required identifier removed from text PDFs",
        "no_identifiers_in_metadata": "no identifier left in file properties",
        "rejected_documents_have_no_output": "nothing produced for rejected files",
    }
    items = "".join(
        f"<li>{theme.status('Yes' if gate[k] else 'No', 'good' if gate[k] else 'bad')}: {v}</li>"
        for k, v in checks.items()
    )
    verdict = theme.status(
        "Pass" if gate["passed"] else "Fail", "good" if gate["passed"] else "bad"
    )
    st.html(f"<p>Release check: {verdict}</p><ul>{items}</ul>")
    oracle = {}
    ref = paths().root / "flow1-testkit" / "metrics" / "reference_oracle_run.txt"
    if ref.exists():
        for line in ref.read_text().splitlines():
            if m := re.match(r"^(variant=\S+)\s+(\d+)%", line):
                oracle[m.group(1)] = f"{m.group(2)}%"
    names = {
        "text_pdf": "Text PDF",
        "xlsx": "Spreadsheet",
        "scan_png_300dpi": "Scan, 300 dpi",
        "scan_png_150dpi": "Scan, 150 dpi, German",
        "scan_jpg_150dpi_lowq": "Poor fax scan",
        "image_pdf_200dpi": "Image-only PDF",
        "image_pdf_rotated_90": "Rotated page",
        "mixed_pdf_image_header": "Identifiers only in an image banner",
        "wrong_extension_png": "Image named .pdf",
        "dicom": "DICOM (not supported yet)",
    }
    rows = []
    for key, v in res["summary"]["rates"].items():
        if not key.startswith("variant=") or not key.endswith("|must_redact"):
            continue
        variant = key.removeprefix("variant=").removesuffix("|must_redact")
        keep = res["summary"]["rates"].get(f"variant={variant}|must_keep", {})
        rows.append(
            {
                "Document": names.get(variant, variant),
                "Identifiers removed": None if v["rate"] is None else f"{v['rate']:.0%}",
                "Without OCR (kit reference)": oracle.get(key, ""),
                "Clinical content kept": None
                if keep.get("rate") is None
                else f"{keep['rate']:.0%}",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


if ctx.case_id is None:
    st.title("Case record")
    states = store.list_case_states()
    if states:
        st.write("Choose a case in the sidebar to see its full record.")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Case": cs.case_id,
                        "Subject": cs.subject,
                        "AE": cs.ae_id,
                        "Status": theme.state_words(cs.state),
                        "Since": theme.date_words(cs.state_since),
                        "Flags": theme.flag_words(cs.flags),
                    }
                    for cs in states
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.write("No cases yet. Import an AE file first.")
    measurements()
    st.stop()

cid = ctx.case_id
case_header(ctx)

# --- package for adjudication ------------------------------------------------------------------
manifest = read_manifest(store, cid)
st.header("Package for adjudication")
if manifest is None:
    st.write("Not ready yet. It is put together as soon as the site signs.")
else:
    s = manifest["submission"]
    st.write(
        f"Signed by {s['signer']} on {theme.date_words(s['signed_at'])} at "
        f"{s['signed_at'][11:16]} UTC: {s['meaning'].split('— ')[-1]}."
    )
    for d in manifest["documents"]:
        name = d["path"].rsplit("/", 1)[-1]
        pages = f", {d['pages']} page{'s' if d['pages'] != 1 else ''}" if d["pages"] else ""
        st.download_button(
            f"Download {name}{pages}", store.get_file(cid, d["path"]), file_name=name, key=d["path"]
        )
    with st.expander("Package contents (flow2_manifest.json)"):
        st.json(manifest)

# --- audit trail -------------------------------------------------------------------------------
st.header("Audit trail")
report = verify_case_log(store.case_dir(cid))
if report.ok:
    st.html(
        f"<p>{theme.status('Intact', 'good')}: all {report.records} entries are chained and "
        "every file they refer to is unchanged.</p>"
    )
else:
    st.html(
        f"<p>{theme.status('Changed after the fact', 'bad')}. The record no longer matches "
        "its seal:</p><ul>" + "".join(f"<li>{p}</li>" for p in report.problems) + "</ul>"
    )
events = store.events(cid)
names = {u["id"]: u["name"] for u in users()}
st.dataframe(
    pd.DataFrame(
        [
            {
                "When (UTC)": f"{theme.date_words(e['at'])} {e['at'][11:19]}",
                "Who": names.get(e["actor"], e["actor"].replace("system:", "System, ")),
                "What": EVENT_WORDS.get(e["type"], e["type"]),
                "Reason": e.get("reason") or "",
                "Seal": e["hash"][7:15],
            }
            for e in events
        ]
    ),
    hide_index=True,
    width="stretch",
)

# --- source data -------------------------------------------------------------------------------
st.header("Source data from the EDC")
tabs = st.tabs(["Adverse event", "Eligibility", "Related data", "Earlier similar events"])
with tabs[0]:
    snap = pipe.latest_snapshot(cid)
    st.dataframe(
        pd.DataFrame(
            [
                {"Field": label, "Value": theme.plain_value(str(snap.get(field, "")))}
                for field, label in theme.AE_FIELDS
            ]
        ),
        hide_index=True,
        width="stretch",
    )
with tabs[1]:
    elig = store.get_json(cid, "source/eligibility_v1.json")
    verdict = "met" if elig["eligible"] else "did not meet"
    st.html(
        f"<p>When the case was created, this AE {verdict} rule set "
        f"{theme.html.escape(str(elig['rule_set']))} version {elig['version']}.</p>"
        f"<ul>{theme.rules_html(elig['results'])}</ul>"
    )
with tabs[2]:
    related = store.get_json(cid, "source/related_data.json")
    demo = related["demographics"]
    st.write(
        f"Sex {demo.get('sex') or 'not recorded'}, age {demo.get('age') or 'not recorded'}, "
        f"first dose {theme.date_words(demo.get('first_dose_date') or '')}. "
        "No name, date of birth or address is pulled from the EDC."
    )
    for key, title in (
        ("medical_history", "Medical history"),
        ("conmeds", "Medication ongoing at onset"),
        ("labs", "Lab results within 30 days of onset"),
    ):
        st.subheader(title)
        rows = [
            {k.replace("_", " ").capitalize(): v for k, v in r.items() if k != "subject_id"}
            for r in related.get(key, [])
        ]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.write("None recorded.")
with tabs[3]:
    prior = store.get_json(cid, "source/prior_events.json")
    if prior:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "AE": e["ae_id"],
                        "Term": e["ae_term"],
                        "Onset": theme.date_words(e["onset_date"]),
                        "Days before this event": e["days_before"],
                        "Matched on": {"PT": "Same preferred term", "SOC": "Same organ class"}.get(
                            e["match"], e["match"]
                        ),
                    }
                    for e in prior
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.write("No earlier similar events for this subject in the 365 days before onset.")

# --- notifications -----------------------------------------------------------------------------
st.header("Notifications")
notes = [json.loads(f.read_text()) for f in sorted(paths().outbox.glob("*.json"))]
mine = [n for n in notes if n.get("case_id") == cid]
emailed: dict[str, str] = {}  # notification id -> last delivery status
delivery = paths().outbox / "delivery.jsonl"
if delivery.exists():
    for line in delivery.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            emailed[d["id"]] = d["status"]
if mine:
    st.write("Messages carry no clinical content: only the case number, the task and a link.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "When (UTC)": f"{theme.date_words(n['created_at'])} {n['created_at'][11:16]}",
                    "To": n["to"]
                    .replace("site:", "Site ")
                    .replace("role:safety_specialist", "Safety Specialist"),
                    "Message": n["subject"],
                    "E-mail": {"sent": "Sent", "failed": "Failed"}.get(
                        emailed.get(n["id"], ""), "Not sent"
                    ),
                }
                for n in mine
            ]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("No notifications for this case.")

# --- AI activity (plan step 12) -----------------------------------------------------------------
st.header("AI activity")
gw = Gateway.for_study(store, paths().config)
calls = [e for e in store.events(cid) if e["type"] == "ai.called"]
mode_words = {"off": "off", "replay": "replaying recorded answers", "live": "live"}
st.write(
    f"AI is {mode_words[gw.cfg.mode]} for this study (model {gw.cfg.model}). Agents only "
    "propose; code checks every proposal before it can move a case."
)
if calls:
    rows = []
    for e in calls:
        d = e["data"]
        rec = store.get_json(cid, d["call"]["path"])
        rows.append(
            {
                "When (UTC)": f"{theme.date_words(e['at'])} {e['at'][11:16]}",
                "Task": d["function"].replace("_", " ").capitalize(),
                "Source": "Simulated AI" if d["simulated"] else "AI",
                "Answer": "Valid" if d["valid"] else "Not usable",
                "Tools used": len(rec.get("tool_calls", [])),
                "Tokens": sum(rec.get("usage", {}).values()),
                "Cost (USD)": f"{d['cost_usd']:.4f}",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
else:
    st.write("No AI calls for this case.")
spent = gw.spent_usd()
st.caption(f"All cases: ${spent:.4f} spent of the ${gw.cfg.budget_usd:.2f} AI budget.")

measurements()
