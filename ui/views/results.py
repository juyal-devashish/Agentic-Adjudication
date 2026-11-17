"""Case record: all cases, and for one case its audit trail with the
chain check, source data, notifications and the package for adjudication. Also the
de-identification measurements on the synthetic test corpus."""

from __future__ import annotations

import difflib
import html
import json
import re
import subprocess
import sys

import pandas as pd
import streamlit as st
import theme
from common import case_header, case_list, context, paths, pipeline, site_name, users

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
    "qc.passed": "Quality check passed automatically",  # cases from before P-37
    "qc.review_needed": "Quality check ready for the Safety Specialist",
    "qc.signed_pass": "Quality check passed and signed",
    "quality_check.signed": "Quality check passed and narrative signed",
    "query.opened": "Query sent to the site",
    "dossier.built": "Dossier built",
    "narrative.drafted": "Narrative drafted",
    "narrative.approved": "Narrative approved by code (AI draft, every statement checked)",
    "narrative.review_needed": "Narrative draft ready for the Safety Specialist",
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
    # Flows 3 and 4
    "adjudication.assigned": "Reviews assigned",
    "adjudication.reassigned": "Review reassigned",
    "adjudication.draft_saved": "Review draft saved",
    "adjudication.signed": "Review signed",
    "comparison.agreed": "Reviews compared: all fields agree",
    "comparison.disputed": "Reviews compared: disputed fields to Adjudicator 3",
    "comparison.to_committee": "Reviews compared: disputed fields to the committee",
    "adj3.signed": "Adjudicator 3 review signed",
    "adj3.resolved": "Adjudicator 3 resolved the disputed fields",
    "adj3.unresolved": "Open fields to the committee",
    "meeting.recorded": "Committee meeting recorded",
    "consensus.signed": "Committee consensus signed",
    "more_data.requested": "More data requested",
    "committee.resumed": "Back to the committee with the new data",
    "outcome.derived": "Outcome derived",
    "export.sent": "Outcome exported to the EDC",
    "export.acknowledged": "EDC acknowledged the outcome; case closed",
    "readjudication.started": "Re-adjudication started",
}
# What a site never sees (ACC-04): who adjudicates and how.
FLOW34_EVENTS = {t for t in EVENT_WORDS if t.split(".")[0] in (
    "adjudication", "comparison", "adj3", "meeting", "consensus", "more_data", "committee",
    "outcome", "export", "readjudication")}  # fmt: skip


# Automatic steps in the audit trail, in words (people are named from users.yaml).
ACTOR_WORDS = {
    "system:intake": "Automatic: case intake",
    "system:deid": "Automatic: identifier removal",
    "system:qc": "Automatic: quality checks",
    "system:narrative": "Automatic: narrative draft",
    "system:dossier": "Automatic: dossier",
    "system:mailer": "Automatic: e-mail",
    "system:upload-folder": "Automatic: upload folder",
    "system:cec": "Automatic: adjudication workflow",
    "system:outcome": "Automatic: outcome and export",
    "system:edc-mock": "EDC (mock)",
}


def who(actor: str, names: dict[str, str]) -> str:
    if actor in names:
        return names[actor]
    if actor.startswith("agent:"):
        return "AI agent, checked by code"
    return ACTOR_WORDS.get(actor, "Automatic")


def recipient(to: str) -> str:
    """'site:101' -> 'Larkfield General Hospital'; 'role:safety_specialist' -> 'Safety
    Specialist'."""
    kind, _, value = to.partition(":")
    if kind == "site":
        return site_name(value)
    if kind == "user":
        return {u["id"]: u["name"] for u in users()}.get(value, value)
    if kind == "mailbox":
        return "Pharmacovigilance team" if value == "pharmacovigilance" else value
    return value.replace("_", " ").title() if kind == "role" else to


def measurements(heading: bool = True) -> None:
    """Study-wide: the same figures on every case, so a case page shows them collapsed."""
    if heading:
        st.header("How well identifiers are removed")
    st.write(
        "Measured on the synthetic test kit: 17 documents with planted identifiers, scored "
        "by the kit's own harness. Scanned pages are read back by OCR, so their figures "
        "are estimates."
    )
    latest = paths().metrics / "latest.json"
    if ctx.role == "safety_specialist" and st.button("Measure again (about 30 seconds)"):
        before = latest.stat().st_mtime if latest.exists() else None
        with st.spinner("Processing the test documents and scoring them…"):
            run = subprocess.run(
                [sys.executable, "-m", "tools.deid_check"],
                check=False,  # noqa: S603
                capture_output=True,
                text=True,
            )
        if not latest.exists() or latest.stat().st_mtime == before:  # a failed gate still writes
            tail = (run.stderr or run.stdout).strip().splitlines()[-3:]
            st.session_state["measure_error"] = " ".join(tail) or f"exit code {run.returncode}"
        st.rerun()
    if error := st.session_state.pop("measure_error", None):
        st.error(f"The measurement did not finish: {error}")
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
    case_list(ctx, "Select a case to open its full record, or choose one in the sidebar.")
    with st.expander("Technical details"):
        measurements(heading=False)
    st.stop()

cid = ctx.case_id
case_header(ctx, "views/results.py")
theme.toc(
    [
        ("package", "Package for adjudication"),
        ("adjudication", "Adjudication and outcome"),
        ("audit-trail", "Audit trail"),
        ("source-data", "Source data"),
        ("notifications", "Notifications"),
        ("ai-activity", "AI activity"),
    ]
)

# --- package for adjudication ------------------------------------------------------------------
manifest = read_manifest(store, cid)
st.header("Package for adjudication", anchor="package")
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


def visible_note(n: dict) -> bool:
    """Flows 3–4 e-mails to a person: not shown to the site (ACC-04), and to an adjudicator
    only when addressed to them."""
    to = n["to"]
    if not to.startswith(("user:", "mailbox:")):
        return True
    if ctx.role == "site":
        return False
    return ctx.role != "adjudicator" or to == f"user:{ctx.actor}"


def show_adjudication(case_id: str) -> None:
    """Rounds, reviews and the outcome with where each value came from (OUT-01, RDJ-05).
    The site sees none of it (ACC-04); an adjudicator sees the comparison and the outcome
    only once the reviews are closed (ACC-02)."""
    from adjud.flow3 import current_round
    from adjud.flow4 import outcomes
    from adjud.workflow import State

    if ctx.role == "site":
        st.write(
            "Adjudication is done by an independent committee. Its details are not shown to sites."
        )
        return
    rnd = current_round(store, case_id)
    if rnd is None:
        st.write("Not started yet. Reviews are assigned once the case is ready for adjudication.")
        return
    names = {u["id"]: u["name"] for u in users()}
    state = store.state(case_id).state
    reviewing = state in (
        State.ADJ_REVIEW,
        State.ADJ3_REVIEW,
        State.MORE_DATA,
        State.QUERY_OPEN,
        State.CEC_MEETING,
    )
    who_ = ", ".join(
        f"Adjudicator {s}: {names.get(u, u)}" + (" (signed)" if s in rnd.signed else "")
        for s, u in sorted(rnd.holders.items())
    )
    st.write(
        f"Round {rnd.number}" + (f", review {rnd.passes}" if rnd.passes > 1 else "") + f". {who_}."
    )
    found = outcomes(store, case_id)
    if ctx.role == "adjudicator" and reviewing:
        theme.quiet("The other reviews and the outcome are shown once the reviews are closed.")
        return
    if not found:
        theme.quiet("No outcome yet.")
        return
    how = {
        "agreed": "Both reviews agree",
        "adjudicator_3": "Adjudicator 3 matched a review",
        "committee": "Committee consensus",
    }
    for o in reversed(found):
        outcome = store.get_json(case_id, o["outcome"]["path"])
        label = f"Outcome of round {o['round']}" + (" (superseded)" if o["superseded"] else "")
        box = st.expander(label, expanded=not o["superseded"])
        with box:
            rows = [
                {
                    "Question": v["label"],
                    "Final answer": v["words"],
                    "Decided by": how[v["how"]],
                    "Signed by": "; ".join(f"{x['role']}: {x['signer']}" for x in v["sources"]),
                }
                for v in outcome["values"].values()
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            if outcome["related"]:
                st.html(
                    f"<p>{theme.status('Related to the study drug', 'bad')}: "
                    "pharmacovigilance was e-mailed (no clinical content).</p>"
                )
            sent = [
                e
                for e in store.events(case_id)
                if e["type"] == "export.sent" and e["data"]["round"] == o["round"]
            ]
            name = f"{case_id}_round{o['round']}.csv"
            acked = [
                e
                for e in store.events(case_id)
                if e["type"] == "export.acknowledged" and e["data"]["ack"]["file"] == name
            ]
            if sent:
                corr = (
                    " as a correction of the earlier export"
                    if sent[0]["data"]["correction"]
                    else ""
                )
                ack = "; not acknowledged yet"
                if acked:
                    a = acked[0]
                    ack = (
                        f"; acknowledged by the {a['data']['ack']['receiver']} on "
                        f"{theme.date_words(a['at'])}"
                    )
                st.write(f"Exported to the EDC{corr} ({sent[0]['data']['to']}){ack}.")
            theme.quiet(
                f"Derivation {outcome['derivation']}, form {outcome['form']['id']} "
                f"version {outcome['form']['version']}."
            )


def narrative_edit(case_id: str) -> None:
    """The Narrative Edit: the draft it started from against the signed text (P-39). Both
    files are in the hash chain; the comparison is only worked out here, never stored."""
    signed = next((e for e in reversed(store.events(case_id))
                   if e["type"] in ("quality_check.signed", "narrative.signed")), None)  # fmt: skip
    if signed is None:
        return
    draft_ref = signed["data"].get("draft") or next(
        (e["data"]["draft"] for e in reversed(store.events(case_id))
         if e["type"] == "narrative.drafted" and e["at"] <= signed["at"]), None)  # fmt: skip
    if draft_ref is None:
        return
    draft = store.get_file(case_id, draft_ref["path"]).decode().strip().splitlines()
    final = store.get_file(case_id, signed["data"]["narrative"]["path"]).decode().strip()
    final_lines = final.splitlines()
    source = {"ai": "the AI draft", "template": "the template"}.get(
        signed["data"].get("source", ""), "the draft"
    )
    with st.expander("Narrative: what the Safety Specialist changed"):
        if draft == final_lines:
            st.write(f"Signed exactly as {source} proposed it.")
            return
        diff = difflib.ndiff(draft, final_lines)
        rows = "".join(
            f'<div class="adj-diff {"del" if ln[0] == "-" else "add"}">'
            f"{'Removed' if ln[0] == '-' else 'Added'}: {html.escape(ln[2:])}</div>"
            for ln in diff if ln[:1] in "-+" and ln[2:].strip()
        )  # fmt: skip
        st.write(f"Changes from {source} to the signed narrative:")
        st.html(rows)


flow3 = next((e for e in reversed(store.events(cid)) if e["type"] == "handoff.flow3_ready"), None)
if flow3:
    package = store.get_json(cid, flow3["data"]["manifest"]["path"])
    n = package["narrative"]
    by = n["approved_by"]
    if by.startswith("agent:"):
        by = "code, from a " + ("simulated AI" if n["simulated_ai"] else "AI") + " draft"
    st.subheader("Ready for adjudication")
    st.write(
        f"Dossier version {package['dossier']['version']} and the narrative approved by "
        f"{by} on {theme.date_words(n['approved_at'])} were handed to adjudication."
    )
    narrative_edit(cid)

# --- adjudication and outcome (Flows 3 and 4) ---------------------------------------------------
st.header("Adjudication and outcome", anchor="adjudication")
show_adjudication(cid)

# --- audit trail -------------------------------------------------------------------------------
st.header("Audit trail", anchor="audit-trail")
report = verify_case_log(store.case_dir(cid))
if report.ok:
    st.html(
        f"<p>{theme.status('Intact', 'good')}: nothing in this record has changed since it "
        f"was written ({report.records} entries, every file checked).</p>"
    )
else:
    st.html(
        f"<p>{theme.status('Changed after the fact', 'bad')}. The record no longer matches "
        "what was written:</p><ul>" + "".join(f"<li>{p}</li>" for p in report.problems) + "</ul>"
    )
events = store.events(cid)
if ctx.role == "site":
    events = [e for e in events if e["type"] not in FLOW34_EVENTS
              and not str(e["data"].get("to", "")).startswith(("user:", "mailbox:"))]  # fmt: skip
names = {u["id"]: u["name"] for u in users()}
# Most entries are automatic steps; a reviewer often wants only what people did
# (uploads, corrections, signatures). The seal check above always covers every entry.
if st.checkbox("Only actions by people", key=f"people_only_{cid}"):
    shown = [e for e in events if not e["actor"].startswith(("system:", "agent:"))]
    theme.quiet(f"Showing {len(shown)} of {len(events)} entries.")
else:
    shown = events
st.dataframe(
    pd.DataFrame(
        [
            {
                "When (UTC)": f"{theme.date_words(e['at'])} {e['at'][11:19]}",
                "Who": who(e["actor"], names),
                "What": EVENT_WORDS.get(e["type"], e["type"]),
                "Reason": e.get("reason") or "",
            }
            for e in shown
        ]
    ),
    hide_index=True,
    width="stretch",
)

# --- source data -------------------------------------------------------------------------------
st.header("Source data from the EDC", anchor="source-data")
tabs = st.tabs(["Adverse event", "Eligibility", "Related data", "Earlier similar events"])
with tabs[0]:
    snap = pipe.latest_snapshot(cid)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Field": label,
                    "Value": site_name(snap.get(field))
                    if field == "site_id"
                    else theme.plain_value(str(snap.get(field, ""))),
                }
                for field, label in theme.AE_FIELDS
                if field not in theme.AE_CODES  # coding details: under Technical details
            ]
        ),
        hide_index=True,
        width="stretch",
    )
with tabs[1]:
    elig = store.get_json(cid, "source/eligibility_v1.json")
    verdict = "met" if elig["eligible"] else "did not meet"
    st.html(
        f"<p>When the case was created, this adverse event {verdict} the study's rules for "
        f"adjudication.</p><ul>{theme.rules_html(elig['results'])}</ul>"
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
st.header("Notifications", anchor="notifications")
notes = [json.loads(f.read_text()) for f in sorted(paths().outbox.glob("*.json"))]
mine = [n for n in notes if n.get("case_id") == cid and visible_note(n)]
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
                    "To": recipient(n["to"]),
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
st.header("AI activity", anchor="ai-activity")
gw = Gateway.for_study(store, paths().config)
calls = [e for e in store.events(cid) if e["type"] == "ai.called"]
mode_words = {"off": "off", "replay": "replaying recorded answers", "live": "live"}
st.write(
    "AI agents read the documents and draft the narrative. They only propose: code checks "
    "every answer, and the Safety Specialist decides."
)
if calls:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "When (UTC)": f"{theme.date_words(e['at'])} {e['at'][11:16]}",
                    "Task": e["data"]["function"].replace("_", " ").capitalize(),
                    "Source": "Simulated AI" if e["data"]["simulated"] else "AI",
                    "Answer": "Valid" if e["data"]["valid"] else "Not usable",
                }
                for e in calls
            ]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("No AI calls for this case.")

# --- technical details: one place, collapsed, for whoever asks ---------------------------------
with st.expander("Technical details"):
    st.subheader("AI")
    st.write(f"AI is {mode_words[gw.cfg.mode]} for this study (model {gw.cfg.model}).")
    if calls:
        rows = []
        for e in calls:
            d = e["data"]
            rec = store.get_json(cid, d["call"]["path"])
            rows.append(
                {
                    "Task": d["function"].replace("_", " ").capitalize(),
                    "Tools used": len(rec.get("tool_calls", [])),
                    "Tokens": sum(rec.get("usage", {}).values()),
                    "Cost (USD)": f"{d['cost_usd']:.4f}",
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(f"All cases: ${gw.spent_usd():.4f} spent of the ${gw.cfg.budget_usd:.2f} AI budget.")
    st.subheader("Audit trail seals")
    st.write("Each entry carries the hash of the one before it, so any change shows.")
    st.dataframe(
        pd.DataFrame(
            [
                {"When (UTC)": f"{theme.date_words(e['at'])} {e['at'][11:19]}",
                 "What": EVENT_WORDS.get(e["type"], e["type"]), "Seal": e["hash"][7:15]}
                for e in events
            ]
        ),
        hide_index=True,
        width="stretch",
    )  # fmt: skip
    st.subheader("Coding and rules")
    st.dataframe(
        pd.DataFrame(
            [
                {"Field": label, "Value": str(snap.get(field, ""))}
                for field, label in theme.AE_FIELDS
                if field in theme.AE_CODES
            ]
            + [{"Field": "Eligibility rule set",
                "Value": f"{elig['rule_set']} version {elig['version']}"}]
        ),
        hide_index=True,
        width="stretch",
    )  # fmt: skip
    if manifest is not None:
        st.subheader("Package for adjudication (flow2_manifest.json)")
        st.json(manifest, expanded=False)
    if flow3:
        st.subheader("Package for Flow 3 (flow3_manifest.json)")
        st.json(package, expanded=False)
    st.subheader("How well identifiers are removed (test kit, every case)")
    measurements(heading=False)
