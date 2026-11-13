"""Case documents through the workflow.

    upload()        site uploads files            -> documents.uploaded  (-> DEIDENTIFYING)
    process_case()  runner de-identifies them all -> deid.started, deid.file_processed per
                    file, then deid.completed (-> AWAITING_SITE_CONFIRMATION) or
                    deid.failed when no file could be processed (-> AWAITING_DOCUMENTS)

Every de-identification run gets its own folders (documents/redacted/r<n>/,
documents/reports/r<n>/), so nothing is ever overwritten and a run interrupted by a
crash is simply followed by run n+1.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePath
from typing import Any

import yaml

from adjud.deid.ocr import png_bytes
from adjud.deid.pipeline import DocInput, deidentify_case
from adjud.deid.policy import Policy
from adjud.events import EventDraft
from adjud.notify import Outbox
from adjud.store import FileCaseStore, json_bytes
from adjud.workflow import State

SYSTEM = "system:deid"
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    base = PurePath(name).name.strip() or "file"
    return SAFE_NAME.sub("_", base)[:120]


def phi_detector(gw: Any, case_id: str, policy: Policy) -> Any:
    """AI-1 as a callback `detect(file, page, image) -> [(text, category)]`: each OCR'd
    page image goes to the agent through `gw` (a Gateway), and every call is recorded on
    `case_id`. Synthetic data only: the images are unredacted."""
    from adjud.ai.functions import PHI_DETECTION
    from adjud.ai.tools import Toolbox
    from adjud.events import sha256_bytes

    categories = {*policy.must_redact, *policy.should_redact, "PERSON"}

    def detect(name: str, page: int, image: Any) -> list[tuple[str, str]]:
        img = image.copy()
        img.thumbnail((2000, 2000))  # enough to read; keeps the request small
        png = png_bytes(img)
        payload = {"file": name, "page": page, "image_sha256": sha256_bytes(png)}
        r = gw.run(case_id, PHI_DETECTION, payload, Toolbox(), images=[png])
        if not r.ok or not r.output:
            return []
        return [
            (i["text"], i["category"] if i["category"] in categories else "PERSON")
            for i in r.output["identifiers"]
            if len(i["text"].strip()) >= 3
        ]

    return detect


@dataclass
class CaseDocument:
    name: str
    path: str
    sha256: str
    label: str | None = None


@dataclass
class CaseDocuments:
    """Current documents of a case and the site's review decisions, from the events."""

    files: dict[str, CaseDocument] = field(default_factory=dict)
    extra_terms: list[str] = field(default_factory=list)
    keep: dict[str, list[str]] = field(default_factory=dict)  # file name -> texts kept
    # file name -> 1-based page -> boxes drawn by the site, as fractions of the page
    boxes: dict[str, dict[int, list[tuple[float, float, float, float]]]] = field(
        default_factory=dict
    )
    runs: int = 0
    last_run: dict[str, Any] | None = None  # data of the last deid.completed / failed
    processed: dict[str, dict[str, Any]] = field(default_factory=dict)  # last run's files


def documents_of(store: FileCaseStore, case_id: str) -> CaseDocuments:
    docs = CaseDocuments()
    current_run: dict[str, dict[str, Any]] = {}
    for e in store.events(case_id):
        d = e.get("data") or {}
        t = e["type"]
        if t == "documents.uploaded":
            for f in d["files"]:
                docs.files[f["name"]] = CaseDocument(f["name"], f["path"], f["sha256"],
                                                     f.get("label"))  # fmt: skip
        elif t == "document.deleted":
            docs.files.pop(d["name"], None)
            docs.keep.pop(d["name"], None)
            docs.boxes.pop(d["name"], None)
        elif t == "redactions.changed":
            if d.get("terms"):  # site-added terms live in a quarantined case file
                text = store.get_file(case_id, d["terms"]["path"]).decode()
                docs.extra_terms += [
                    x for x in text.splitlines() if x.strip() and x not in docs.extra_terms
                ]
            for name, ids in (d.get("keep") or {}).items():
                docs.keep.setdefault(name, [])
                docs.keep[name] += [x for x in ids if x not in docs.keep[name]]
            for b in d.get("boxes") or []:
                docs.boxes.setdefault(b["file"], {}).setdefault(b["page"], []).append(
                    tuple(b["rect"])
                )
        elif t == "deid.started":
            docs.runs = d["run"]
            current_run = {}
        elif t == "deid.file_processed":
            current_run[d["name"]] = d
        elif t in ("deid.completed", "deid.failed"):
            docs.last_run = d
            docs.processed = current_run
    return docs


def upload(store: FileCaseStore, case_id: str, files: list[tuple[str, bytes]], actor: str,
           labels: dict[str, str] | None = None,
           via: dict[str, Any] | None = None) -> list[str]:  # fmt: skip
    """Store originals (quarantine, never overwritten) and record the upload. Returns the
    stored names; a name already used in the case gets a numeric suffix."""
    existing = documents_of(store, case_id).files
    taken = set(existing) | {
        p.name for p in (store.case_dir(case_id) / "documents" / "original").glob("*")
    }
    entries = []
    for raw_name, content in files:
        name = safe_filename(raw_name)
        stem, suffix, n = PurePath(name).stem, PurePath(name).suffix, 2
        while name in taken:
            name, n = f"{stem}-{n}{suffix}", n + 1
        taken.add(name)
        rel = f"documents/original/{name}"
        sha = store.put_file(case_id, rel, content)
        entry = {"name": name, "path": rel, "sha256": sha, "size": len(content)}
        if labels and raw_name in labels:
            entry["label"] = labels[raw_name]
        entries.append(entry)
    data: dict[str, Any] = {"files": entries}
    if via:  # how the files arrived, e.g. {"zip": name, "sha256": ...} from an upload folder
        data["via"] = via
    store.append_event(case_id, EventDraft(type="documents.uploaded", actor=actor, data=data))
    return [e["name"] for e in entries]


def seeded_values(identities: dict[str, Any], subject: str) -> list[str]:
    """The generator's seeded identifiers for one subject (synthetic data only)."""
    s = identities.get(subject)
    if not s:
        return []
    values = [f"{s['first']} {s['last']}", s["last"], s["address"], s["phone"], s["mobile"],
              s["email"], s["mrn"], s["insurance"], s["kin"]]  # fmt: skip
    if s.get("dob"):
        d = date.fromisoformat(s["dob"])
        values += [d.strftime("%d/%m/%Y"), d.isoformat()]
    return [v for v in values if v]


@dataclass
class DocumentProcessor:
    store: FileCaseStore
    config_dir: Path
    study: str = "DEMO01"

    def __post_init__(self) -> None:
        self.policy = Policy.for_study(self.config_dir, self.study)
        self.study_cfg = yaml.safe_load(
            (Path(self.config_dir) / self.study / "study.yaml").read_text()
        )
        self.outbox = Outbox(self.store.paths, self.study, clock=self.store.clock)
        rel = self.study_cfg.get("seeded_identities")
        path = self.store.paths.root / rel if rel else None
        self.identities = json.loads(path.read_text())["subjects"] if path and path.exists() else {}

    def _event(self, case_id: str, type_: str, data: dict[str, Any]) -> None:
        self.store.append_event(case_id, EventDraft(type=type_, actor=SYSTEM, data=data))

    def _notify(self, case_id: str, to: str, template: str) -> None:
        note = self.outbox.queue(case_id, to, template)
        self._event(case_id, "notification.queued",
                    {"to": to, "template": template, "id": note["id"]})  # fmt: skip

    def _ai_phi(self, case_id: str) -> Any:
        """AI-1 (plan step 20) as a callback for the redaction of OCR'd pages, or None when
        the study has it off. It sees unredacted page images: synthetic data only."""
        from adjud.ai.functions import PHI_DETECTION
        from adjud.ai.gateway import Gateway

        gw = Gateway.for_study(self.store, self.config_dir, self.study)
        if not gw.cfg.enabled(PHI_DETECTION.name):
            return None
        return phi_detector(gw, case_id, self.policy)

    def process_case(self, case_id: str) -> dict[str, Any]:
        """One de-identification run over all current documents of a DEIDENTIFYING case."""
        store = self.store
        cs = store.state(case_id)
        if cs.state != State.DEIDENTIFYING:
            raise ValueError(f"{case_id} is {cs.state}, not DEIDENTIFYING")
        docs = documents_of(store, case_id)
        run = docs.runs + 1
        self._event(case_id, "deid.started", {"run": run, "files": sorted(docs.files)})
        limits = self.study_cfg.get("upload_limits", {})
        inputs = [
            DocInput(
                name=f.name,
                content=store.get_file(case_id, f.path),
                extra_terms=docs.extra_terms,
                keep=docs.keep.get(f.name, []),
                boxes=docs.boxes.get(f.name, {}),
            )
            for f in docs.files.values()
        ]
        results = deidentify_case(
            inputs, self.policy, seeded=seeded_values(self.identities, cs.subject or ""),
            max_file_mb=int(limits.get("max_file_mb", 50)),
            max_pdf_pages=int(limits.get("max_pdf_pages", 200)),
            ai_detect=self._ai_phi(case_id),
        )  # fmt: skip
        statuses = {}
        for r in results:
            data: dict[str, Any] = {
                "name": r.name,
                "kind": r.kind,
                "status": r.status,
                "reject_code": r.reject_code,
                "warnings": r.warnings,
                "findings": len(r.findings),
                "residuals": len(r.residuals),
                "pages": r.pages,
                "output": None,
            }
            if r.output is not None and r.output_name:
                rel = f"documents/redacted/r{run}/{r.output_name}"
                data["output"] = {"path": rel, "sha256": store.put_file(case_id, rel, r.output)}
            rel = f"documents/reports/r{run}/{r.name}.json"
            report_sha = store.put_file(case_id, rel, json_bytes(r.report()))
            data["report"] = {"path": rel, "sha256": report_sha}
            self._event(case_id, "deid.file_processed", data)
            statuses[r.name] = r.status
        site = f"site:{(cs.subject or '--').split('-')[1]}"
        summary = {"run": run, "files": statuses,
                   "all_verified": bool(statuses) and all(
                       s == "verified" for s in statuses.values())}  # fmt: skip
        processed = [s for s in statuses.values() if s in ("verified", "blocked")]
        if any(s == "rejected" for s in statuses.values()):
            self._notify(case_id, site, "processing_failed")
        if not processed:
            self._event(case_id, "deid.failed", summary)
        else:
            self._event(case_id, "deid.completed", summary)
            if not self._after_site_correction(case_id):
                self._notify(case_id, site, "ready_for_review")
        return summary

    def _after_site_correction(self, case_id: str) -> bool:
        """True when this run follows a correction or removal the site made on the page:
        the site is looking at it already, so no 'ready for your review' message."""
        causes = ("documents.uploaded", "redactions.changed", "document.deleted")
        last = next((e["type"] for e in reversed(self.store.events(case_id))
                     if e["type"] in causes), None)  # fmt: skip
        return last in ("redactions.changed", "document.deleted")

    def run_pending(self) -> list[str]:
        """Process every case waiting in DEIDENTIFYING. Returns the case ids processed."""
        done = []
        for cs in self.store.list_case_states(State.DEIDENTIFYING):
            self.process_case(cs.case_id)
            done.append(cs.case_id)
        return done
