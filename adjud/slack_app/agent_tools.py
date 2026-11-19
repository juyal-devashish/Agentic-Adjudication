"""The Slack Agent's tools, one toolbox per role (plan P-58, Q5, Q11, ADR 0005).

The Agent sees exactly what the person it acts for sees in the app, and this is enforced
here, in code, never only in its prompt: a tool the role does not have is not in its
toolbox, and every tool that takes a case resolves it among that person's cases only
(`Workspace.find_case`), so a site never reaches another site's case and an adjudicator
only the cases assigned to them. "Not yours" and "no such case" get the same answer.

What the tools read: case records (events), EDC snapshots, the intake log, Quality
Check results, the dossier's page map, narrative drafts and the de-identification
reports, as counts. Never `documents/original/`, never an unverified file, and never
the reports' masked fragments ("Ph… Ro…"): the redaction results are counts per file.

    everyone            my_tasks, my_cases, case_status
    data manager        + waiting_files, records_not_a_case, case_summary
    site                + case_summary, required_documents, redaction_results, query_text
    Safety Specialist   + case_summary, qc_checks, ai_suggestion, dossier_summary,
                          narrative_draft, query_text
    CEC Coordinator, adjudicator: tasks and status only (Flows 3–4 have no Agent tools)

The unsigned steps (Q4) act as that person and are ordinary case events, as from the
app; none signs, and none moves a case the app's same step would not:

    data manager        import_waiting_files, pull_from_edc (recorded in the Slack
                        actions log, as `/adj import` is)
    site                upload_attached_files: the files attached to this message, to a
                        case of its site that takes documents (Q8; `files.py`)
    Safety Specialist   edit_narrative_draft, draft_site_query: unsigned drafts that
                        pre-fill the Quality check page (`flow2.qc_drafts`); the case
                        stays in QC review until it is signed there

What a turn brings (the Slack user, the attached files) comes in a `Turn`, never in the
tools' definitions: those stay fixed per role, so a recorded answer is found again.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from adjud.ai.tools import Tool, Toolbox, ToolError, _schema
from adjud.documents import documents_of, upload
from adjud.dossier import latest_dossier
from adjud.flow2 import QC_QUERY_CODES, QUERY_CODES, Flow2, Flow2Refused, qc_drafts
from adjud.qc import Checklist
from adjud.slack_app.core import SlackActionsLog, Workspace, import_as, suggested_word
from adjud.slack_app.files import FileRefused, SlackFiles
from adjud.workflow import TRANSITIONS, State, state_words

# De-identification categories in plain words, plural, so a count never reads like a
# label followed by a value ("address 1") to the PHI gate's label rules.
CATEGORY_WORDS = {
    "PATIENT_NAME": "patient names",
    "NEXT_OF_KIN": "next-of-kin names",
    "DOB": "dates of birth",
    "MRN": "hospital record numbers",
    "INSURANCE_NO": "insurance numbers",
    "ADDRESS": "addresses",
    "PHONE": "phone numbers",
    "EMAIL": "e-mail addresses",
    "CASE_REFERENCE": "case references",
    "STAFF_NAME": "staff names",
    "STAFF_CONTACT": "staff contacts",
    "FACILITY": "hospital names and addresses",
    "PERSON": "other names",
    "LOCATION": "places",
    "SITE_TERM": "terms the site added",
    "SEEDED": "seeded test identifiers",
}
CASE_ARG = {"case": {"type": "string",
                     "description": "Case ID, e.g. DEMO01-0001 or 0001"}}  # fmt: skip
SNAPSHOT_FIELDS = (
    "subject_id", "ae_id", "ae_term", "onset_date", "end_date", "serious",
    "seriousness_criteria", "severity", "outcome", "relatedness_investigator",
    "pt_code", "soc_code",
)  # fmt: skip


# The states in which a site may add documents, as the Documents page offers the upload
UPLOAD_STATES = frozenset(s for s in TRANSITIONS["documents.uploaded"] if s is not None)
SIGN_IN_APP = "Signed in the app: the reply's Open in the app button opens the page."


@dataclass
class Turn:
    """What one Slack turn gives the acting tools, and what they did (for the logs)."""

    slack_user: str = ""
    actions: SlackActionsLog | None = None  # the Slack actions log, as for /adj import
    attachments: list[dict[str, Any]] = field(default_factory=list)  # files in the thread
    files: SlackFiles | None = None  # fetches and deletes them
    done: list[dict[str, Any]] = field(default_factory=list)  # for the conversation log


def agent_toolbox(ws: Workspace, person: dict[str, Any], turn: Turn | None = None) -> Toolbox:
    """The tools of the person the Agent acts for (Q11)."""
    return AgentTools(ws, person, turn).toolbox()


class AgentTools:
    def __init__(self, ws: Workspace, person: dict[str, Any], turn: Turn | None = None) -> None:
        self.ws, self.person, self.role = ws, person, person["role"]
        self.turn = turn or Turn()

    def case(self, args: dict[str, Any]) -> str:
        """The case named in `args`, among this person's cases only; the same refusal
        whether it is someone else's or does not exist (Q5)."""
        text = str(args.get("case") or "").strip()
        case_id = self.ws.find_case(self.person, text) if text else None
        if case_id is None:
            raise ToolError(f"no case {text!r} among the cases this person can see")
        return case_id

    def toolbox(self) -> Toolbox:
        tools = [
            self._tool("my_tasks", "This person's tasks: what waits for them, on which case, "
                       "and whether it is done in Slack or in the app.", {}, self.my_tasks),
            self._tool("my_cases", "The cases this person can see, with where each is.", {},
                       self.my_cases),
            self._tool("case_status", "Where one case is, since when, at which site, and "
                       "this person's next step on it.", CASE_ARG, self.case_status),
        ]  # fmt: skip
        summary = self._tool("case_summary", "The EDC record of one case (pseudonymised: "
                             "subject ID, AE term, dates, seriousness, outcome, codes) and "
                             "any flags.", CASE_ARG, self.case_summary)  # fmt: skip
        query = self._tool("query_text", "The Site Query on one case, if any: its reasons, "
                           "its text, when it was sent and whether it is still open.",
                           CASE_ARG, self.query_text)  # fmt: skip
        if self.role == "data_manager":
            tools += [
                self._tool("waiting_files", "AE files waiting in the import queue.", {},
                           self.waiting_files),
                self._tool("records_not_a_case", "AE records that are not a case, with the "
                           "study rules each did not meet. `ae_id` narrows it to one record "
                           "(e.g. AE-008); empty for all.",
                           {"ae_id": {"type": "string", "description": "AE ID or empty"}},
                           self.records_not_a_case),
                summary,
                self._tool("import_waiting_files", "Import the AE files waiting in the queue, "
                           "as this person. Only when they ask for it.", {},
                           self.import_waiting_files),
                self._tool("pull_from_edc", "Pull the AE records from the EDC now and import "
                           "them, as this person. Only when they ask for it.", {},
                           self.pull_from_edc),
            ]  # fmt: skip
        elif self.role == "site":
            tools += [
                summary,
                self._tool("required_documents", "The documents one case needs, which are "
                           "uploaded and which are missing.", CASE_ARG,
                           self.required_documents),
                self._tool("redaction_results", "De-identification results per file of one "
                           "case: status, pages, how many identifiers were removed by kind, "
                           "how many remain or were kept. Counts only.", CASE_ARG,
                           self.redaction_results),
                query,
                self._tool("upload_attached_files", "Add the files the person attached in "
                           "this conversation to one case of their site, as this person; "
                           "identifiers are then removed automatically. Only when they ask "
                           "for it or answer a query with files.", CASE_ARG,
                           self.upload_attached_files),
            ]  # fmt: skip
        elif self.role == "safety_specialist":
            tools += [
                summary,
                self._tool("qc_checks", "The latest Quality Check round of one case: each "
                           "check and its result, and the Suggested Outcome.", CASE_ARG,
                           self.qc_checks),
                self._tool("ai_suggestion", "The AI Suggestion of the latest Quality Check "
                           "round: what the documents say for each EDC field, and documents "
                           "that would help. Advice; the Safety Specialist decides.",
                           CASE_ARG, self.ai_suggestion),
                self._tool("dossier_summary", "The latest dossier of one case: version and "
                           "its documents with page ranges.", CASE_ARG, self.dossier_summary),
                self._tool("narrative_draft", "The narrative draft of one case, where it "
                           "came from and whether it was approved.", CASE_ARG,
                           self.narrative_draft),
                query,
                self._tool("edit_narrative_draft", "Save the narrative of one case in QC review "
                           "as edited, unsigned. `text` is the whole narrative. It pre-fills "
                           "the Quality check page, where it is signed.",
                           {**CASE_ARG, "text": {"type": "string",
                                                 "description": "The whole narrative"}},
                           self.edit_narrative_draft),
                self._tool("draft_site_query", "Draft the Site Query of one case in QC "
                           "review, unsigned: its reasons and the message to the site. It "
                           "pre-fills the Quality check page; nothing reaches the site until "
                           "it is signed there.",
                           {**CASE_ARG,
                            "reasons": {"type": "array", "description": "Query reasons",
                                        "items": {"type": "string", "enum": QC_QUERY_CODES}},
                            "text": {"type": "string", "description": "Message to the site"}},
                           self.draft_site_query),
            ]  # fmt: skip
        return Toolbox(tools)

    @staticmethod
    def _tool(name: str, description: str, props: dict[str, Any],
              run: Callable[[dict[str, Any]], Any]) -> Tool:  # fmt: skip
        return Tool(name, description, _schema(props), run)

    # -- everyone -------------------------------------------------------------------------

    def my_tasks(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"tasks": [
            {"case": t.case_id, "task": t.words,
             "where": "here in Slack" if t.in_slack else "in the app"}
            for t in self.ws.tasks(self.person)
        ]}  # fmt: skip

    def my_cases(self, _: dict[str, Any]) -> dict[str, Any]:
        out = []
        for cs in self.ws.cases(self.person):
            s = self.ws.status(self.person, cs.case_id)
            out.append({"case": s.case_id, "state": s.state, "since": s.since, "site": s.site})
        return {"cases": out}

    def case_status(self, args: dict[str, Any]) -> dict[str, Any]:
        s = self.ws.status(self.person, self.case(args))
        out = {"case": s.case_id, "state": s.state, "since": s.since, "site": s.site,
               "your_next_step": s.next_step}  # fmt: skip
        if self.role == "safety_specialist" and self._state(s.case_id) == State.QC_REVIEW:
            out["suggested_outcome"] = self.ws.suggested(s.case_id)
        return out

    # -- data manager -----------------------------------------------------------------------

    def waiting_files(self, _: dict[str, Any]) -> dict[str, Any]:
        files = self.ws.waiting_files()
        return {"count": len(files), "files": [f.name for f in files]}

    def records_not_a_case(self, args: dict[str, Any]) -> dict[str, Any]:
        """The latest evaluation of each AE record that is not eligible and not a case."""
        want = str(args.get("ae_id") or "").strip().upper()
        latest: dict[str, dict[str, Any]] = {}
        for e in self.ws.store.intake_log().records():
            latest[e["key"]] = e
        out = []
        for key, e in latest.items():
            if e.get("case_id") or e.get("eligible") is not False:
                continue
            _, subject, ae_id = (key.split("|") + ["", ""])[:3]
            if want and ae_id.upper() != want:
                continue
            out.append({"subject": subject, "ae_id": ae_id, "evaluated": str(e["at"])[:10],
                        "rules_not_met": failed_rules(e.get("explanation") or [])})  # fmt: skip
        return {"records": out}

    def case_summary(self, args: dict[str, Any]) -> dict[str, Any]:
        case_id = self.case(args)
        snap = self.ws.pipe.latest_snapshot(case_id)
        cs = self.ws.store.state(case_id)
        return {
            "case": case_id,
            **{k: snap.get(k) for k in SNAPSHOT_FIELDS if snap.get(k)},
            "site": self.ws.people.site_name(snap.get("site_id")),
            "flags": cs.flags,
        }

    # -- site --------------------------------------------------------------------------------

    def required_documents(self, args: dict[str, Any]) -> dict[str, Any]:
        case_id = self.case(args)
        checklist = Checklist.for_study(self.ws.paths.config, self.ws.study)
        required = checklist.required_for(self.ws.pipe.latest_snapshot(case_id))
        docs = documents_of(self.ws.store, case_id)
        uploaded = []
        for name in sorted(docs.files):
            type_, _ = docs.type_of(name)
            uploaded.append({"file": name, "type": checklist.label(type_) if type_ else None})
        have = {docs.type_of(n)[0] for n in docs.files}
        return {
            "case": case_id,
            "required": [checklist.label(t) for t in required],
            "uploaded": uploaded,
            "missing": [checklist.label(t) for t in required if t not in have],
        }

    def redaction_results(self, args: dict[str, Any]) -> dict[str, Any]:
        """Counts per file from the last de-identification run; the reports' identifier
        fragments are never read out."""
        case_id = self.case(args)
        store = self.ws.store
        docs = documents_of(store, case_id)
        files = []
        for name in sorted(docs.files):
            d = docs.processed.get(name)
            if d is None:
                files.append({"file": name, "status": "not processed yet"})
                continue
            row: dict[str, Any] = {"file": name, "status": d["status"], "pages": d.get("pages"),
                                   "identifiers_removed": d.get("findings", 0),
                                   "identifiers_left": d.get("residuals", 0),
                                   "kept_by_the_site": len(docs.keep.get(name, []))}  # fmt: skip
            if d.get("reject_code"):
                row["rejected_because"] = d["reject_code"].replace("_", " ").lower()
            if d.get("report"):
                report = store.get_json(case_id, d["report"]["path"])
                row["removed_by_kind"] = _counts(report.get("findings") or [])
                if report.get("residuals"):
                    row["left_by_kind"] = _counts(report["residuals"])
            files.append(row)
        run = docs.last_run or {}
        return {"case": case_id, "state": self.ws.status(self.person, case_id).state,
                "run": run.get("run"), "all_verified": bool(run.get("all_verified")),
                "files": files}  # fmt: skip

    def query_text(self, args: dict[str, Any]) -> dict[str, Any]:
        case_id = self.case(args)
        q = next((e for e in reversed(self.ws.store.events(case_id))
                  if e["type"] == "query.opened"), None)  # fmt: skip
        draft = qc_drafts(self.ws.store, case_id) if self.role == "safety_specialist" else None
        if draft is not None and draft.query_at and self._state(case_id) == State.QC_REVIEW:
            return {"case": case_id, "query": None, "unsigned_draft": {
                "reasons": [QUERY_CODES.get(c, c) for c in draft.query_codes],
                "text": draft.query_text or "", "signed": SIGN_IN_APP}}  # fmt: skip
        if q is None:
            return {"case": case_id, "query": None}
        return {"case": case_id, "query": {
            "reasons": [QUERY_CODES.get(c, c) for c in q["data"].get("codes", [])],
            "text": q["data"].get("text", ""),
            "sent": str(q["at"])[:10],
            "open": self._state(case_id) == State.QUERY_OPEN,
        }}  # fmt: skip

    # -- Safety Specialist -------------------------------------------------------------------

    def _qc(self, case_id: str) -> dict[str, Any]:
        e = next((e for e in reversed(self.ws.store.events(case_id))
                  if e["type"] == "qc.checks_completed"), None)  # fmt: skip
        if e is None:
            raise ToolError(f"{case_id} has no Quality Check yet")
        return self.ws.store.get_json(case_id, e["data"]["results"]["path"])

    def qc_checks(self, args: dict[str, Any]) -> dict[str, Any]:
        case_id = self.case(args)
        r = self._qc(case_id)
        return {
            "case": case_id,
            "round": r["round"],
            "checks": [
                {"check": c["label"], "result": c["status"], "detail": c.get("detail")}
                for c in r["checks"]
            ],  # fmt: skip
            "suggested_outcome": suggested_word(r.get("proposal")),
            "reasons": r.get("reasons") or [],
            "waiting_for_review": self._state(case_id) == State.QC_REVIEW,
        }

    def ai_suggestion(self, args: dict[str, Any]) -> dict[str, Any]:
        case_id = self.case(args)
        r = self._qc(case_id)
        sg = r.get("suggestion") or {}
        if sg.get("status") != "ok":
            return {"case": case_id, "round": r["round"], "suggestion": None,
                    "note": sg.get("note") or "no AI Suggestion this round"}  # fmt: skip
        return {
            "case": case_id,
            "round": r["round"],
            "source": sg.get("label"),  # "AI" or "Simulated AI"
            "summary": sg.get("summary", ""),
            "fields": [
                {
                    "field": f["label"],
                    "edc": f.get("edc_value"),
                    "documents": f.get("document_value"),
                    "status": f["status"],
                    "reason": f.get("reason", ""),
                }
                for f in sg.get("fields", [])
            ],  # fmt: skip
            "would_help": [d.get("label") or d.get("type") for d in sg.get("would_help", [])],
            "suggested_outcome": suggested_word(r.get("proposal")),
        }

    def dossier_summary(self, args: dict[str, Any]) -> dict[str, Any]:
        case_id = self.case(args)
        d = latest_dossier(self.ws.store, case_id)
        if d is None:
            raise ToolError(f"{case_id} has no dossier yet")
        checklist = Checklist.for_study(self.ws.paths.config, self.ws.study)
        return {"case": case_id, "version": d["version"], "documents": [
            {"file": p["name"], "type": checklist.label(p["type"]) if p.get("type") else None,
             "first_page": p["first_page"], "pages": p["pages"]}
            for p in d["page_map"]
        ]}  # fmt: skip

    def narrative_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        case_id = self.case(args)
        events = self.ws.store.events(case_id)
        drafted = next((e for e in reversed(events) if e["type"] == "narrative.drafted"), None)
        if drafted is None:
            raise ToolError(f"{case_id} has no narrative draft yet")
        # once signed, the signed file is the narrative (it may differ from the draft)
        approval = next((e for e in reversed(events)
                         if e["type"] in ("quality_check.signed", "narrative.signed",
                                          "narrative.approved")
                         and e["seq"] > drafted["seq"] and e["data"].get("narrative")),
                        None)  # fmt: skip
        approved = approval is not None
        d = drafted["data"]
        edited = qc_drafts(self.ws.store, case_id).narrative if not approved else None
        path = approval["data"]["narrative"]["path"] if approval else d["draft"]["path"]
        return {
            "case": case_id,
            "text": edited or self.ws.store.get_file(case_id, path).decode(),
            "source": {"ai": "AI", "template": "study template"}.get(d.get("source"), "?"),
            "simulated": bool(d.get("simulated")),
            "edited_unsigned": edited is not None,
            "approved": approved,
        }

    # -- acting: unsigned steps only (Q4) ------------------------------------------------

    def _import(self, action: str) -> dict[str, Any]:
        if self.turn.actions is None:
            raise ToolError("importing is not available here")
        reply = import_as(self.ws, self.turn.actions, self.turn.slack_user, self.person,
                          action, via="agent")  # fmt: skip
        self.turn.done.append({"action": action, "result": reply.result,
                               "cases": reply.created})  # fmt: skip
        if reply.result != "done":
            raise ToolError(reply.text)
        return {"done": reply.text, "new_cases": reply.created}

    def import_waiting_files(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._import("import")

    def pull_from_edc(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._import("pull")

    def upload_attached_files(self, args: dict[str, Any]) -> dict[str, Any]:
        """The site's upload from Slack (Q8, Q13): the same call as the Documents page,
        as this person, then Slack is asked to delete the original."""
        case_id = self.case(args)
        state = self._state(case_id)
        if state not in UPLOAD_STATES:
            raise ToolError(f"{case_id} does not take documents now "
                            f"({state_words(state, self.role)})")  # fmt: skip
        if not self.turn.attachments or self.turn.files is None:
            raise ToolError("no file is attached in this conversation")
        # a Slack file goes into one case once: never again, to this case or another
        before = {i for cid in self.ws.store.list_cases() for e in self.ws.store.events(cid)
                  if e["type"] == "documents.uploaded"
                  for i in ((e["data"].get("via") or {}).get("slack_files") or [])}  # fmt: skip
        fresh = [f for f in self.turn.attachments if f.get("id") not in before]
        if not fresh:
            raise ToolError("the files attached in this conversation are already in the case "
                            "they were added to")  # fmt: skip
        got: list[tuple[str, bytes, str]] = []
        refused = []
        for n, f in enumerate(fresh, 1):
            try:
                name, content = self.turn.files.fetch(f)
            except FileRefused as exc:
                refused.append({"attachment": n, "why": str(exc)})
                continue
            got.append((name, content, str(f.get("id"))))
        names = []
        if got:
            via = {"slack_files": [i for *_, i in got]}  # never added twice
            names = upload(self.ws.store, case_id, [(n, c) for n, c, _ in got],
                           self.person["id"], via=via)  # fmt: skip
        deleted = {i: self.turn.files.delete(i) for *_, i in got}
        said = sorted({v for v in deleted.values() if v != "deleted"})
        detail = {"files": len(got), "refused": len(refused),
                  "deleted_from_slack": sum(v == "deleted" for v in deleted.values()),
                  "slack_said": said}  # fmt: skip
        if self.turn.actions is not None:
            self.turn.actions.record(
                self.turn.slack_user, self.person["id"], "agent:upload", cases=[case_id],
                result="done" if got else "refused",
                reason=None if got else "no file could be added", detail=detail,
            )  # fmt: skip
        self.turn.done.append({"action": "upload", "case": case_id, **detail})
        if not got:
            raise ToolError("no file was added: " + "; ".join(
                f"attachment {r['attachment']}: {r['why']}" for r in refused))  # fmt: skip
        kept = len(got) - detail["deleted_from_slack"]
        return {
            "case": case_id,
            "added": names,
            "not_added": refused,
            "next": "Identifiers are being removed now; the redacted pages are then ready "
                    "to review and sign in the app.",
            "original_in_slack": (f"{kept} original file(s) stay in Slack: the app cannot "
                                  "delete a person's file there") if kept else
                                 "The originals were deleted from Slack.",
        }  # fmt: skip

    def _flow2(self) -> Flow2:
        return Flow2(self.ws.store, self.ws.paths.config, self.ws.study)

    def edit_narrative_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        case_id = self.case(args)
        try:
            text = str(args.get("text") or "")
            self._flow2().save_narrative_draft(case_id, self.person["id"], text, via="slack")
        except Flow2Refused as exc:
            raise ToolError(str(exc)) from None
        self.turn.done.append({"action": "narrative_draft", "case": case_id})
        return {"case": case_id, "saved": "narrative draft, unsigned", "signed": SIGN_IN_APP}

    def draft_site_query(self, args: dict[str, Any]) -> dict[str, Any]:
        case_id = self.case(args)
        codes = [str(c) for c in args.get("reasons") or []]
        try:
            text = str(args.get("text") or "")
            self._flow2().save_query_draft(case_id, self.person["id"], codes, text, via="slack")
        except Flow2Refused as exc:
            raise ToolError(str(exc)) from None
        self.turn.done.append({"action": "query_draft", "case": case_id})
        return {"case": case_id, "saved": "Site Query draft, unsigned; not sent to the site",
                "signed": SIGN_IN_APP}  # fmt: skip

    def _state(self, case_id: str) -> State:
        return self.ws.store.state(case_id).state


def _counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Findings by kind, in words. Only the category is read, never the masked text."""
    c = Counter(CATEGORY_WORDS.get(f.get("category", ""), "other identifiers") for f in findings)
    return dict(sorted(c.items()))


def failed_rules(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """One entry per top-level rule not met: its id, description and why. A group joins
    its unmet branches ("any": all of them failed; "all": the ones that did)."""
    out = []
    for r in results:
        if r.get("passed"):
            continue
        out.append({"rule": str(r.get("id") or ""), "description": str(r.get("description") or ""),
                    "why": "; ".join(_why(r))})  # fmt: skip
    return out


def _why(r: dict[str, Any]) -> list[str]:
    if "children" in r:
        return [w for c in r["children"] if not c.get("passed") for w in _why(c)]
    op = str(r.get("operator") or "").replace("_", " ")
    return [f"{r.get('field')} is {r.get('actual')!r}, rule: {op} {r.get('expected')!r} "
            f"({r.get('reason') or 'not met'})"]  # fmt: skip
