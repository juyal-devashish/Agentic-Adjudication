"""Deterministic quality checks for Flow 2 (plan step 14, QC-02, P-32).

Every check reads only the Flow 2 manifest and the redacted files and reports it lists.
Each check ends `passed`, `failed` (a clear problem; it carries the query reason the
site is sent) or `unknown` (code cannot tell; the QC agent or a person decides).

    readable              every document has readable text
    identifiers_left      every document passed verification after redaction
    document_types        each document's type: site label, else the first title keyword
    required_documents    the types this event needs are all there
    same_patient          no two documents name different patients, and no document
                          carries another subject's ID
    subject_and_onset     the subject ID and a date near the onset appear somewhere
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from adjud.ai.tools import output_pages
from adjud.deid.detect import DATE_RE
from adjud.deid.identity import dob_keys
from adjud.store import FileCaseStore

MIN_TEXT = 20  # characters; less than this and a document is not readable


@dataclass
class Check:
    id: str
    label: str
    status: str  # passed | failed | unknown
    detail: str = ""
    query_code: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass
class QcOutcome:
    checks: list[Check]
    types: dict[str, dict[str, str]] = field(default_factory=dict)  # file -> {type, source}

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "failed"]

    @property
    def unknown(self) -> list[Check]:
        return [c for c in self.checks if c.status == "unknown"]

    def as_dicts(self) -> list[dict[str, Any]]:
        return [{**asdict(c), "passed": c.passed} for c in self.checks]


@dataclass
class Checklist:
    types: dict[str, dict[str, Any]]
    required: dict[str, Any]
    onset_window_days: int = 3

    @classmethod
    def for_study(cls, config_dir: Path, study: str) -> Checklist:
        raw = yaml.safe_load((Path(config_dir) / study / "document_checklist.yaml").read_text())
        return cls(raw["types"], raw["required"], int(raw.get("onset_window_days", 3)))

    def label(self, type_: str) -> str:
        return self.types.get(type_, {}).get("label", type_.replace("_", " "))

    def required_for(self, ae: dict[str, Any]) -> list[str]:
        if (ae.get("outcome") or "").upper() == "FATAL":
            return list(self.required.get("fatal") or self.required["default"])
        by_soc = self.required.get("by_soc") or {}
        return list(by_soc.get(str(ae.get("soc_code")), self.required["default"]))

    def keyword_type(self, first_page: str, *, spreadsheet: bool = False) -> str | None:
        """The type named by the document's title: the first title line (mostly capitals,
        as clinical letters print them) holding a keyword, as a whole word. Body text is
        not used, so "Catheter Laboratory" in a letterhead or "the ECG showed" in a
        summary do not decide the type. Spreadsheets have no title; all text counts."""
        lines = [first_page] if spreadsheet else [ln for ln in first_page.splitlines()
                                                    if _is_title(ln)]  # fmt: skip
        for line in lines:
            hits = [
                (m.start(), type_)
                for type_, spec in self.types.items()
                for kw in spec.get("keywords") or []
                if (
                    m := re.search(
                        rf"(?<![a-z0-9]){re.escape(kw.lower())}(?![a-z0-9])", line.lower()
                    )
                )  # fmt: skip
            ]
            if hits:
                return min(hits)[1]
        return None


def _is_title(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) >= 0.8


def _ids(text: str, study: str) -> set[str]:
    """Subject IDs of this study in the text, in canonical form. OCR reads 0 as O and
    sometimes doubles it (DEMOO1, DEMOO01), so each O/0 of the prefix matches one or two
    of either."""
    prefix = "".join("[O0]{1,2}" if ch in "O0" else re.escape(ch) for ch in study.upper())
    found = re.findall(rf"{prefix}-([0-9O]{{3}})-([0-9O]{{4}})", text.upper())
    return {f"{study.upper()}-{a.replace('O', '0')}-{b.replace('O', '0')}" for a, b in found}


def _dates(text: str) -> set[date]:
    out = set()
    for m in DATE_RE.finditer(text):
        for d, mth, yy in dob_keys(m.group()):
            try:
                out.add(date(2000 + yy if yy < 70 else 1900 + yy, mth, d))
            except ValueError:
                continue
    return out


def run_checks(
    store: FileCaseStore,
    case_id: str,
    manifest: dict[str, Any],
    checklist: Checklist,
    *,
    ai_types: dict[str, str] | None = None,
    event: dict[str, str] | None = None,
) -> QcOutcome:
    """`ai_types` (file -> type) and `event` ({"verdict", "reason"}) are agent answers
    that already passed their gate (step 15); they fill in only what code left unknown."""
    ai_types = ai_types or {}
    ae = store.get_json(case_id, manifest["ae_snapshot"]["path"])
    docs = manifest["documents"]
    texts = {d["source_name"]: output_pages(store, case_id, d["path"]) for d in docs}
    checks: list[Check] = []

    # readable
    unreadable = [n for n, pages in texts.items() if len("".join(pages).strip()) < MIN_TEXT]
    checks.append(
        Check(
            "readable",
            "Every document can be read",
            "failed" if unreadable else "passed",
            f"No readable text in {', '.join(unreadable)}" if unreadable else "",
            "unreadable" if unreadable else None,
        )  # fmt: skip
    )

    # identifiers_left
    left = [d["source_name"] for d in docs if d.get("verification") != "passed"]
    checks.append(
        Check(
            "identifiers_left",
            "No identifiers left after redaction",
            "failed" if left else "passed",
            f"Verification did not pass for {', '.join(left)}" if left else "",
            "identifiers_left" if left else None,
        )  # fmt: skip
    )

    # document_types
    types: dict[str, dict[str, str]] = {}
    for d in docs:
        name = d["source_name"]
        if d.get("document_type"):
            types[name] = {"type": d["document_type"], "source": "site label"}
        elif kw := checklist.keyword_type(
            texts[name][0] if texts[name] else "", spreadsheet=d["path"].endswith(".xlsx")
        ):
            types[name] = {"type": kw, "source": "title"}
        elif name in ai_types:
            types[name] = {"type": ai_types[name], "source": "AI"}
        else:
            types[name] = {"type": "unknown", "source": ""}
    untyped = [n for n, t in types.items() if t["type"] == "unknown"]
    checks.append(
        Check(
            "document_types",
            "Every document's type is known",
            "unknown" if untyped else "passed",
            f"Type not recognised: {', '.join(untyped)}"
            if untyped
            else "; ".join(f"{n}: {checklist.label(t['type'])}" for n, t in types.items()),
        )  # fmt: skip
    )

    # required_documents
    present = {t["type"] for t in types.values()}
    missing = [r for r in checklist.required_for(ae) if r not in present]
    if not missing:
        req = Check("required_documents", "Required documents are present", "passed")
    elif untyped:  # an untyped document might be the missing one
        req = Check("required_documents", "Required documents are present", "unknown",
                    f"Not found yet: {', '.join(checklist.label(m) for m in missing)}")  # fmt: skip
    else:
        req = Check("required_documents", "Required documents are present", "failed",
                    f"Missing: {', '.join(checklist.label(m) for m in missing)}",
                    "missing_document")  # fmt: skip
    checks.append(req)

    # same_patient
    problems = []
    for d in docs:
        report = store.get_json(case_id, d["redaction_report"])
        ident = report.get("identity") or {}
        for kind, word in (("dob", "date of birth"), ("name", "name")):
            for other in ident.get(kind, []):
                if d["source_name"] < other:
                    problems.append(f"{d['source_name']} and {other} give a different {word}")
    subject = str(ae.get("subject_id", ""))
    for name, pages in texts.items():
        others = _ids("\n".join(pages), manifest["study"]) - _ids(subject, manifest["study"])
        if others:
            problems.append(f"{name} names another subject ({', '.join(sorted(others))})")
    checks.append(
        Check(
            "same_patient",
            "All documents are about this patient",
            "failed" if problems else "passed",
            "; ".join(problems),
            "wrong_patient" if problems else None,
        )  # fmt: skip
    )

    # subject_and_onset
    everything = "\n".join("\n".join(p) for p in texts.values())
    subject_found = bool(_ids(everything, manifest["study"]) & _ids(subject, manifest["study"]))
    onset_found = False
    if ae.get("onset_date"):
        onset = date.fromisoformat(ae["onset_date"])
        window = timedelta(days=checklist.onset_window_days)
        onset_found = any(abs(d - onset) <= window for d in _dates(everything))
    missing_bits = [w for w, ok in (("subject ID", subject_found), ("onset date", onset_found))
                    if not ok]  # fmt: skip
    label = "The documents describe this event (subject ID and onset date)"
    if not missing_bits:
        checks.append(Check("subject_and_onset", label, "passed"))
    elif event and event["verdict"] == "matches":
        checks.append(Check("subject_and_onset", label, "passed", f"AI: {event['reason']}"))
    elif event and event["verdict"] == "different_event":
        reason = f"AI: {event['reason']}"
        checks.append(Check("subject_and_onset", label, "failed", reason, "wrong_event"))
    else:
        detail = f"Not found: {' and '.join(missing_bits)}"
        checks.append(Check("subject_and_onset", label, "unknown", detail))
    return QcOutcome(checks, types)
