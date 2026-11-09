"""Spreadsheet de-identification.

- Every cell on every sheet, including hidden sheets, rows and columns, is scanned.
- Columns whose header is an identifier label (Patient Name, Hospital no, DOB, ...) are
  redacted entirely; so is the cell to the right of a label in key/value layouts.
- Redacted text becomes "[REDACTED]". Formulas are replaced by their (redacted) cached
  values, so no formula can rebuild a redacted value.
- Comments, document properties, external links and defined names are removed.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.packaging.core import DocumentProperties

from adjud.deid.detect import Finding, KnownValues, detect, finding_id, learn, merge_spans
from adjud.deid.pagetext import TextUnit
from adjud.deid.policy import Policy
from adjud.deid.textmatch import norm

REDACTED = "[REDACTED]"

# Header/key labels that make a whole column or the next cell an identifier. Narrower than
# PROPAGATE: a "Reference" column in a lab sheet holds reference ranges, not case numbers.
CELL_LABEL_CATEGORIES = frozenset(
    {"PATIENT_NAME", "NEXT_OF_KIN", "DOB", "MRN", "INSURANCE_NO", "ADDRESS", "PHONE", "EMAIL"}
)


def _label_category(text: str, policy: Policy) -> str | None:
    """Category if a header/key cell is an identifier label ("Patient Name" -> name)."""
    t = norm(text).strip(" :")
    if not t:
        return None
    for category, labels in policy.labels.items():
        if category not in CELL_LABEL_CATEGORIES:
            continue
        for label in labels:
            lab = norm(label).strip(" :.")
            contained = len(lab) > 3 and re.search(rf"(?<!\w){re.escape(lab)}(?!\w)", t)
            if t == lab or contained:
                return category
    return None


def _cell_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return None  # dates/times stay as they are (dob_only: clinical dates are kept)


@dataclass
class XlsxOutcome:
    output: bytes
    findings: list[Finding] = field(default_factory=list)
    redacted_cells: list[str] = field(default_factory=list)


def _forced_cells(wb: Workbook, policy: Policy) -> dict[tuple[str, str], str]:
    """(sheet, coordinate) -> category for whole identifier columns and key/value cells."""
    forced: dict[tuple[str, str], str] = {}
    for ws in wb.worksheets:
        header = {c.column: _label_category(str(c.value), policy) for c in ws[1] if c.value}
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if header.get(c.column) and c.value not in (None, ""):
                    forced[(ws.title, c.coordinate)] = header[c.column]
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and (cat := _label_category(c.value, policy)):
                    right = ws.cell(row=c.row, column=c.column + 1)
                    if right.value not in (None, ""):
                        forced[(ws.title, right.coordinate)] = cat
    return forced


def learn_xlsx(content: bytes, policy: Policy, known: KnownValues) -> None:
    wb = load_workbook(io.BytesIO(content), data_only=True)
    for (sheet, coord), category in _forced_cells(wb, policy).items():
        text = _cell_text(wb[sheet][coord].value)
        if text:
            known.add(category, text)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if text := _cell_text(c.value):
                    learn(TextUnit.from_string(text, f"{ws.title}!{c.coordinate}"), policy, known)


def _site_terms(terms: list[str] | None) -> KnownValues:
    extra = KnownValues()
    for term in terms or []:
        if term.strip():
            extra.values[term.strip()] = "SITE_TERM"
    return extra


def redact_xlsx(
    content: bytes,
    policy: Policy,
    known: KnownValues,
    *,
    extra_terms: list[str] | None = None,
    keep: list[str] | None = None,
) -> XlsxOutcome:
    """`extra_terms`: site-added terms; `keep`: ids of findings the site unticked."""
    extra, keep_ids = _site_terms(extra_terms), set(keep or [])
    wb = load_workbook(io.BytesIO(content))  # formulas
    cached = load_workbook(io.BytesIO(content), data_only=True)  # last computed values
    forced = _forced_cells(cached, policy)
    outcome = XlsxOutcome(output=b"")
    for ws in wb.worksheets:
        wv = cached[ws.title]
        for row in ws.iter_rows():
            for c in row:
                where = f"{ws.title}!{c.coordinate}"
                value = c.value
                if c.data_type == "f" or (isinstance(value, str) and value.startswith("=")):
                    value = wv[c.coordinate].value  # formula -> its cached value
                    c.value = value
                if c.comment is not None:
                    c.comment = None
                category = forced.get((ws.title, c.coordinate))
                if category is not None and finding_id(str(value)) not in keep_ids:
                    found = Finding(0, 0, category, "column", str(value), source=where)
                    outcome.findings.append(found)
                    outcome.redacted_cells.append(where)
                    c.value = REDACTED
                    continue
                text = _cell_text(value)
                if not text:
                    continue
                unit = TextUnit.from_string(text, where)
                findings = [
                    f
                    for f in detect(unit, policy, known) + extra.find(unit)
                    if f.id not in keep_ids
                ]
                if findings:
                    outcome.findings += findings
                    outcome.redacted_cells.append(where)
                    for s, e in sorted(merge_spans(findings), reverse=True):
                        text = text[:s] + REDACTED + text[e:]
                    c.value = text
    for name in list(wb.defined_names):
        del wb.defined_names[name]
    for ws in wb.worksheets:
        for name in list(ws.defined_names):
            del ws.defined_names[name]
    wb._external_links = []  # noqa: SLF001 - openpyxl has no public API for this
    wb.properties = DocumentProperties(creator="adjud de-identification")
    buf = io.BytesIO()
    wb.save(buf)
    outcome.output = buf.getvalue()
    return outcome


def xlsx_texts(content: bytes) -> list[TextUnit]:
    """Every text the harness would read: cells (formula and cached), comments,
    properties, defined names."""
    units = []
    wb_f = load_workbook(io.BytesIO(content))
    wb_v = load_workbook(io.BytesIO(content), data_only=True)
    for ws in wb_f.worksheets:
        for row in ws.iter_rows():
            for c in row:
                for v in (c.value, wb_v[ws.title][c.coordinate].value):
                    if (t := _cell_text(v)) and t != REDACTED:
                        units.append(TextUnit.from_string(t, f"{ws.title}!{c.coordinate}"))
                if c.comment:
                    units.append(TextUnit.from_string(c.comment.text, f"comment:{c.coordinate}"))
    p = wb_f.properties
    for attr in ("creator", "title", "subject", "description", "keywords", "lastModifiedBy",
                 "category"):  # fmt: skip
        if v := getattr(p, attr, None):
            units.append(TextUnit.from_string(str(v), f"property:{attr}"))
    for name, d in wb_f.defined_names.items():
        units.append(TextUnit.from_string(f"{name}={d.attr_text}", f"name:{name}"))
    return units


@dataclass
class XlsxVerification:
    residuals: list[Finding] = field(default_factory=list)
    comments: int = 0
    defined_names: int = 0

    @property
    def passed(self) -> bool:
        return not (self.residuals or self.comments or self.defined_names)

    def summary(self) -> dict[str, Any]:
        return {"passed": self.passed, "residuals": len(self.residuals),
                "comments": self.comments, "defined_names": self.defined_names}  # fmt: skip


def verify_xlsx(
    output: bytes,
    policy: Policy,
    known: KnownValues,
    *,
    extra_terms: list[str] | None = None,
    keep: list[str] | None = None,
) -> XlsxVerification:
    result = XlsxVerification()
    extra, keep_ids = _site_terms(extra_terms), set(keep or [])
    for unit in xlsx_texts(output):
        if unit.source.startswith("comment:"):
            result.comments += 1
        elif unit.source.startswith("name:"):
            result.defined_names += 1
        elif unit.source == "property:creator" and unit.text == "adjud de-identification":
            continue
        result.residuals += [
            f for f in detect(unit, policy, known) + extra.find(unit) if f.id not in keep_ids
        ]
    return result
