"""The Assistant (P-40): a guided chat in front of the workflow, answered by code.

Each person asks in plain words or with a slash action (/tasks, /status 0001, /why AE-008,
/qc 0001, /import); code reads the case records and answers, quoting what the AI already
wrote (the AI Suggestion, the narrative draft), labelled as such. For every manual step
the answer carries a link to the page where the person does it: upload, review, sign.
The Assistant never signs, submits, passes a Quality Check or sends a query (ADR 0001);
its one action is the data manager's import, the same as the button on AE import.
Nothing here calls the API, so it works the same with AI off, on replay or live.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import theme
from common import doc_label, pipeline, site_name, study_config
from wording import IMPORT_OUTCOME, import_summary, in_sentence, next_step

from adjud.documents import documents_of
from adjud.qc import Checklist
from adjud.review import submission_status
from adjud.store import read_log
from adjud.workflow import State

PAGES = {  # page key -> file, as registered in app.py
    "assistant": "views/assistant.py",
    "import": "views/edc_input.py",
    "documents": "views/site_documents.py",
    "qc": "views/quality_check.py",
    "record": "views/results.py",
}
HELP = (
    "I can help with:\n"
    "- **/tasks**: what is waiting for you\n"
    "- **/status 0001**: where a case is and what happens next\n"
    "- **/qc 0001**: the quality check and the AI Suggestion for a case\n"
    "- **/why AE-008**: why a record did or did not become a case\n"
    "- **/import**: import the AE records waiting in the queue (data manager)\n\n"
    'You can also just ask, for example *"why is AE-008 not a case?"*.'
)


@dataclass
class Action:
    """A link to the page where a person does a step themselves."""

    label: str
    page: str
    case_id: str | None = None


@dataclass
class Reply:
    text: str
    actions: list[Action] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "text": self.text,
            "actions": [a.__dict__ for a in self.actions],
        }


@dataclass
class Assistant:
    user: dict[str, Any]

    def __post_init__(self) -> None:
        self.pipe = pipeline()
        self.store = self.pipe.store
        self.role = self.user["role"]

    # -- what this person may see ------------------------------------------------------------

    def sees(self, subject: str | None) -> bool:
        if self.role != "site":
            return True
        parts = (subject or "").split("-")
        return len(parts) > 1 and parts[1] == self.user.get("site")

    def cases(self) -> list[Any]:
        return [cs for cs in self.store.list_case_states() if self.sees(cs.subject)]

    def find_case(self, text: str) -> str | None:
        """A case named in the text: DEMO01-0001, 0001, or an AE ID such as AE-001."""
        ids = {cs.case_id: cs for cs in self.cases()}
        if m := re.search(r"\b([A-Z0-9]+-\d{4})\b", text.upper()):
            if m.group(1) in ids:
                return m.group(1)
        if m := re.search(r"\bAE-?(\d{3})\b", text.upper()):
            ae = f"AE-{m.group(1)}"
            return next((c for c, cs in ids.items() if cs.ae_id == ae), None)
        if m := re.search(r"\b(\d{4})\b", text):
            return next((c for c in ids if c.endswith(f"-{m.group(1)}")), None)
        return None

    # -- the router --------------------------------------------------------------------------

    def handle(self, text: str) -> Reply:
        t = text.strip()
        low = t.lower()
        command = low.split()[0] if low.startswith("/") else ""
        case_id = self.find_case(t)
        if command in ("/help", "/start") or low in ("help", "hi", "hello"):
            return self.greeting() if command == "/start" else Reply(HELP)
        if command in ("/tasks", "/my-tasks") or re.search(
            r"\b(my tasks?|to do|todo|waiting for me|what do i need|what should i)\b", low
        ):
            return self.tasks()
        if command == "/import" or (not command and re.search(r"\bimport\b", low)):
            return self.run_import()
        if command == "/why" or (not command and re.search(r"\b(why|eligib|not a case)", low)):
            return self.why(t)
        if command == "/qc" or (not command and re.search(r"\b(qc|quality|suggest)", low)):
            return self.quality_check(case_id) if case_id else self.ask_for_case("/qc")
        if command == "/status" or case_id:
            return self.status(case_id) if case_id else self.ask_for_case("/status")
        return Reply("I didn't catch that. " + HELP)

    def ask_for_case(self, command: str) -> Reply:
        ids = ", ".join(cs.case_id for cs in self.cases()) or "none yet"
        return Reply(f"Which case? For example `{command} 0001`. Your cases: {ids}.")

    # -- answers -----------------------------------------------------------------------------

    def greeting(self) -> Reply:
        first = (
            self.user["name"].split()[-1]
            if self.role == "safety_specialist"
            else (self.user["name"].split()[0])
        )
        hello = f"Hello {'Dr. ' if self.role == 'safety_specialist' else ''}{first}. "
        tasks = self.tasks()
        return Reply(hello + tasks.text + "\n\nType **/help** to see what I can do.", tasks.actions)

    def tasks(self) -> Reply:
        lines: list[str] = []
        actions: list[Action] = []
        if self.role == "data_manager":
            waiting = sorted(f.name for f in self.pipe_paths().inbox.glob("*") if f.is_file())
            if waiting:
                lines.append(
                    f"**{len(waiting)} AE file{'s' if len(waiting) > 1 else ''} "
                    f"waiting to be imported**: {', '.join(waiting)}. Type **/import** "
                    "and I'll import it now."
                )
            for cs in self.cases():
                if cs.flags:
                    lines.append(f"**{cs.case_id}** is flagged: {theme.flag_words(cs.flags)}.")
                    actions.append(Action(f"Check {cs.case_id}", "import", cs.case_id))
        for cs in self.cases():
            line, action = self._task(cs)
            if line:
                lines.append(line)
            if action:
                actions.append(action)
        if not lines:
            return Reply(
                "Nothing is waiting for you right now. I'll tell you here when something comes in."
            )
        n = len(lines)
        head = f"You have {n} thing{'s' if n > 1 else ''} waiting:\n"
        return Reply(head + "\n".join(f"- {ln}" for ln in lines), actions)

    def _task(self, cs: Any) -> tuple[str | None, Action | None]:
        snap = self.pipe.latest_snapshot(cs.case_id)
        onset = theme.date_words(snap["onset_date"])
        what = f"**{cs.case_id}** ({snap['ae_term'].lower()}, onset {onset})"
        if self.role == "site":
            if cs.state == State.AWAITING_DOCUMENTS:
                needed = self.required_documents(cs.case_id)
                return (
                    f"{what} needs documents: {needed}.",
                    Action(f"Upload documents for {cs.case_id}", "documents", cs.case_id),
                )
            if cs.state == State.AWAITING_SITE_CONFIRMATION:
                return (
                    f"{what}: identifiers were removed and checked again. Review the "
                    "redacted pages and sign.",
                    Action(f"Review and sign {cs.case_id}", "documents", cs.case_id),
                )
            if cs.state == State.QUERY_OPEN:
                q = self._latest(cs.case_id, "query.opened") or {}
                from adjud.flow2 import QUERY_CODES

                why = ", ".join(QUERY_CODES.get(c, c).lower() for c in q.get("codes", []))
                return (
                    f"{what}: the Safety Specialist sent a query ({why}). "
                    f"{q.get('text', '')}".strip(),
                    Action(f"Answer the query for {cs.case_id}", "documents", cs.case_id),
                )
        if self.role == "safety_specialist":
            if cs.state == State.QC_REVIEW:
                return (
                    f"{what} is ready for your Quality Check. {self._suggestion_line(cs.case_id)}",
                    Action(f"Review and sign {cs.case_id}", "qc", cs.case_id),
                )
            if cs.state == State.NARRATIVE_REVIEW:
                return (
                    f"{what}: the narrative draft is ready to check and sign.",
                    Action(f"Complete the narrative for {cs.case_id}", "qc", cs.case_id),
                )
        return None, None

    def status(self, case_id: str) -> Reply:
        cs = self.store.state(case_id)
        snap = self.pipe.latest_snapshot(case_id)
        step = next_step(
            self.role,
            cs.state,
            site=site_name(snap["site_id"]),
            requested=[doc_label(r) for r in study_config().get("requested_documents", [])],
            ready=submission_status(self.store, case_id).ready,
            has_files=bool(documents_of(self.store, case_id).files),
        )
        lines = [
            f"**{case_id}**: {snap['ae_term']}, subject {snap['subject_id']} at "
            f"{site_name(snap['site_id'])}, onset {theme.date_words(snap['onset_date'])}.",
            f"Status: **{theme.state_words(cs.state)}** since {theme.date_words(cs.state_since)}.",
            step,
        ]
        if cs.state == State.QC_REVIEW and self.role != "site":
            lines.append(self._suggestion_line(case_id))
        if cs.state == State.READY_FOR_ADJUDICATION:
            signed = self._latest_event(case_id, "quality_check.signed") or self._latest_event(
                case_id, "narrative.signed"
            )
            if signed and signed.get("signature"):
                lines.append(
                    f"Quality Check and narrative signed by {signed['signature']['signer']}."
                )
        _, action = self._task(cs)
        actions = [action] if action else []
        actions.append(Action(f"Open the case record of {case_id}", "record", case_id))
        return Reply("\n\n".join(lines), actions)

    def quality_check(self, case_id: str) -> Reply:
        if self.role == "site":
            return Reply(
                "The quality check is the Safety Specialist's review. I can tell "
                f"you where the case is: `/status {case_id}`."
            )
        result = self._qc_result(case_id)
        if result is None:
            return Reply(
                f"{case_id} has no quality check yet. It starts once the site has "
                "signed and submitted."
            )
        sg = result.get("suggestion") or {}
        lines = [f"**Quality check of {case_id}**, round {result['round']}."]
        checks = result["checks"]
        failed = [c for c in checks if c["status"] == "failed"]
        unsure = [c for c in checks if c["status"] == "unknown"]
        lines.append(
            f"Checks: {len(checks) - len(failed) - len(unsure)} of {len(checks)} passed"
            + (f"; failed: {', '.join(c['label'].lower() for c in failed)}" if failed else "")
            + "."
        )
        if sg.get("status") == "ok":
            lines.append(f"**AI Suggestion** ({sg['label']}): {sg.get('summary', '')}")
            docs = sg.get("documents") or {}
            if docs.get("status") == "problem":
                lines.append(f"Documents: {docs.get('detail')}.")
            for f in sg.get("fields", []):
                if f["status"] == "mismatch":
                    lines.append(
                        f"- {f['label']} doesn't match: EDC {f['edc_value']}, documents "
                        f"{f['document_value']}."
                    )
                elif f["status"] == "not_sure":
                    lines.append(f"- {f['label']}: not sure. {f['reason']}")
            if docs.get("would_help"):
                helps = ", ".join(d["label"] for d in docs["would_help"])
                lines.append(f"Would also help (advice only): {helps}.")
        else:
            lines.append(f"No AI Suggestion this round ({sg.get('note') or 'AI is off'}).")
        lines.append(self._suggestion_line(case_id))
        nar = result.get("narrative") or {}
        if nar:
            source = "the AI" if nar.get("source") == "ai" else "the study template"
            lines.append(f"The narrative draft, from {source}, is ready to edit.")
        lines.append("You decide and sign; the AI only suggests.")
        actions = []
        if self.store.state(case_id).state == State.QC_REVIEW and self.role == "safety_specialist":
            actions.append(Action(f"Review and sign {case_id}", "qc", case_id))
        return Reply("\n\n".join(lines), actions)

    def why(self, text: str) -> Reply:
        m = re.search(r"\bAE-?(\d{3})\b", text.upper())
        if not m:
            return Reply("Which record? For example `/why AE-008`.")
        ae = f"AE-{m.group(1)}"
        latest: dict[str, Any] | None = None
        for e in read_log(self.store.evaluations_path):
            key = e.get("key", "")
            if key.endswith(f"|{ae}") and e.get("explanation") and self.sees(key.split("|")[1]):
                latest = e
        if latest is None:
            return Reply(f"I have no record of {ae} being imported yet.")
        subject = latest["key"].split("|")[1]
        failed = _failed_rules(latest["explanation"])
        if latest["eligible"]:
            text = (
                f"**{ae}** ({subject}) meets the study's rules, so it became case "
                f"**{latest.get('case_id')}**."
            )
            action = [Action(f"Open {latest.get('case_id')}", "record", latest.get("case_id"))]
            return Reply(text, action)
        rules = "\n".join(f"- Not met: {r}" for r in failed)
        return Reply(
            f"**{ae}** ({subject}) does not meet the study's rules for "
            f"adjudication, so it is not a case:\n{rules}\n\nAll other rules "
            "were met.",
            [Action("Open the import results", "import")] if self.role == "data_manager" else [],
        )

    def run_import(self) -> Reply:
        if self.role != "data_manager":
            return Reply(
                "Importing AE records is the data manager's job, so I can't do it "
                "for you. I'll let you know here when new cases arrive."
            )
        paths = self.pipe_paths()
        if not any(f.is_file() for f in paths.inbox.glob("*")):
            return Reply(
                "There is no AE file waiting in the import queue.",
                [Action("Go to AE import", "import")],
            )
        results = self.pipe.process_inbox(paths.inbox, paths.processed, actor=self.user["id"])
        lines = []
        for r in results:
            lines.append(import_summary({**r.summary(), "file": r.file}))
            for o in r.outcomes:
                subject, ae = o["key"].split("|")[1:3]
                word = IMPORT_OUTCOME.get(o["outcome"], o["outcome"])
                if o.get("case_id"):
                    lines.append(f"- {ae} ({subject}): {word.lower()} **{o['case_id']}**")
                else:
                    lines.append(f"- {ae} ({subject}): {word.lower()}. Ask me `/why {ae}`.")
        if any(o.get("case_id") for r in results for o in r.outcomes):
            lines.append("\nThe sites are e-mailed now with a link to upload their documents.")
        return Reply("\n".join(lines), [Action("Open the import results", "import")])

    # -- live updates ------------------------------------------------------------------------

    def updates(self, seen: dict[str, int]) -> list[Reply]:
        """Messages for what happened since `seen` (case -> last event seq shown), in the
        order it happened, for the cases this person may see. Updates `seen`."""
        out: list[tuple[str, Reply]] = []
        for cs in self.cases():
            last = seen.get(cs.case_id, 0)
            for e in self.store.events(cs.case_id):
                if e["seq"] <= last:
                    continue
                if reply := self._update(cs.case_id, e):
                    out.append((e["at"], reply))
            seen[cs.case_id] = cs.seq
        return [r for _, r in sorted(out, key=lambda x: x[0])]

    def _update(self, case_id: str, e: dict[str, Any]) -> Reply | None:
        t, ss, site = e["type"], self.role == "safety_specialist", self.role == "site"
        who = (e.get("signature") or {}).get("signer") or e["actor"]
        if t == "case.created":
            return Reply(
                f"New case **{case_id}**: {e['data'].get('ae_id')}, subject "
                f"{e['data'].get('subject')}."
            )
        if t == "documents.uploaded":
            return Reply(f"{case_id}: documents received. Removing identifiers…")
        if t == "deid.completed":
            text = f"{case_id}: identifiers removed and checked again from scratch."
            if site:
                return Reply(
                    text + " Ready for your review and signature.",
                    [Action(f"Review and sign {case_id}", "documents", case_id)],
                )
            return Reply(text + " Waiting for the site's review.")
        if t == "submission.signed":
            return Reply(
                f"{case_id}: signed and submitted by {who}. The quality check is "
                "running: the checks, the AI Suggestion and the narrative draft…"
            )
        if t == "qc.review_needed":
            if site:
                return Reply(f"{case_id}: your documents are with the Safety Specialist.")
            line = f"{case_id}: the quality check is ready. {self._suggestion_line(case_id)}"
            return Reply(line, [Action(f"Review and sign {case_id}", "qc", case_id)] if ss else [])
        if t == "query.opened":
            if site:
                return Reply(
                    f"{case_id}: the Safety Specialist sent you a query.",
                    [Action(f"Answer the query for {case_id}", "documents", case_id)],
                )
            return Reply(f"{case_id}: query sent to the site by {who}.")
        if t == "quality_check.signed":
            return Reply(f"{case_id}: quality check passed and narrative signed by {who}.")
        if t == "handoff.flow3_ready":
            return Reply(f"{case_id} is **ready for adjudication**. The package is sealed.")
        return None

    # -- helpers -----------------------------------------------------------------------------

    def pipe_paths(self) -> Any:
        return self.store.paths

    def required_documents(self, case_id: str) -> str:
        checklist = Checklist.for_study(self.store.paths.config, self.pipe.study)
        snap = self.pipe.latest_snapshot(case_id)
        return " and ".join(in_sentence(checklist.label(t)) for t in checklist.required_for(snap))

    def _latest_event(self, case_id: str, type_: str) -> dict[str, Any] | None:
        return next((e for e in reversed(self.store.events(case_id)) if e["type"] == type_), None)

    def _latest(self, case_id: str, type_: str) -> dict[str, Any] | None:
        e = self._latest_event(case_id, type_)
        return e["data"] if e else None

    def _qc_result(self, case_id: str) -> dict[str, Any] | None:
        e = self._latest(case_id, "qc.checks_completed")
        return self.store.get_json(case_id, e["results"]["path"]) if e else None

    def _suggestion_line(self, case_id: str) -> str:
        result = self._qc_result(case_id)
        if result is None:
            return ""
        proposal = result.get("proposal")
        reasons = "; ".join(result.get("reasons") or [])
        if proposal == "pass":
            unsure = [
                f["label"].lower()
                for f in (result.get("suggestion") or {}).get("fields", [])
                if f["status"] == "not_sure"
            ]
            note = f" ({', '.join(unsure)} not sure)" if unsure else ""
            return f"Suggested outcome: **pass**: the documents match the EDC{note}."
        if proposal == "query":
            return f"Suggested outcome: **send a query**: {reasons}."
        return f"No suggested outcome: something is not sure ({reasons})."


def _failed_rules(results: list[dict[str, Any]]) -> list[str]:
    out = []
    for r in results:
        if r["passed"]:
            continue
        if "children" in r:
            out += _failed_rules(r["children"]) or [r.get("description", r.get("id", ""))]
        else:
            value = theme.plain_value(str(r.get("actual") if r.get("actual") is not None else ""))
            field = dict(theme.AE_FIELDS).get(r["field"], r["field"])
            out.append(f"{r.get('description', r['id'])} ({field}: {value})")
    return out
