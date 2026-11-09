"""De-identify all documents of one case.

Two passes over the case's files:
  1. learn: every labelled or pattern-matched identifier in any file is remembered
     (KnownValues), so a name found in one document's banner is also removed from
     another document's footer, body text or image banner;
  2. per file: validate -> redact -> scrub -> verify from scratch.

Each file ends as one of:
  verified   output written, verification found nothing
  blocked    output written, but verification found a residual (or could not check
             image content yet); the site must act before submitting
  rejected   no output (encrypted, corrupt, unsupported, too large)
  pending    accepted, but no processor exists for its type
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from adjud.deid.detect import KnownValues
from adjud.deid.identity import DocIdentity, conflicts
from adjud.deid.pdf import (
    Box,
    learn_image,
    learn_pdf,
    redact_image,
    redact_pdf,
    verify_pdf,
)
from adjud.deid.policy import Policy
from adjud.deid.sniff import check_upload
from adjud.deid.xlsx import learn_xlsx, redact_xlsx, verify_xlsx


@dataclass
class DocInput:
    name: str
    content: bytes
    extra_terms: list[str] = field(default_factory=list)  # site-added terms (F1-22)
    keep: list[str] = field(default_factory=list)  # finding ids the site kept (F1-21)
    boxes: dict[int, list[Box]] = field(default_factory=dict)  # site-drawn areas by page


@dataclass
class DocResult:
    name: str
    kind: str
    status: str  # verified | blocked | rejected | pending
    output_name: str | None = None
    output: bytes | None = None
    reject_code: str | None = None
    message: str | None = None
    warnings: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    residuals: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    pages: int | None = None
    # other files of the case that name a different patient (P-32); never the values
    identity: dict[str, list[str]] = field(default_factory=dict)

    def report(self) -> dict[str, Any]:
        """The JSON report written next to the redacted file (no identifiers in it)."""
        return {
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "output_name": self.output_name,
            "reject_code": self.reject_code,
            "message": self.message,
            "warnings": self.warnings,
            "pages": self.pages,
            "findings": self.findings,
            "residuals": self.residuals,
            "verification": self.verification,
            "identity": self.identity,
        }


def output_name(name: str, kind: str) -> str:
    """Images come back as PDFs; everything else keeps its name."""
    p = PurePath(name)
    return f"{p.stem}.pdf" if kind in ("image", "pdf") else p.name


def deidentify_case(
    docs: list[DocInput], policy: Policy, *, known: KnownValues | None = None,
    seeded: list[str] | None = None, max_file_mb: int = 50, max_pdf_pages: int = 200,
    ai_detect: Callable[[str, int, Any], list[tuple[str, str]]] | None = None,
) -> list[DocResult]:  # fmt: skip
    """`ai_detect(file, page, image)`: AI-1, only when the study switches it on (synthetic
    data only); it can add findings on OCR'd pages, never remove any."""
    known = known or KnownValues()
    checks = {d.name: check_upload(d.name, d.content, max_file_mb=max_file_mb,
                                   max_pdf_pages=max_pdf_pages) for d in docs}  # fmt: skip

    identities: dict[str, DocIdentity] = {}
    for d in docs:  # pass 1: learn from every accepted file
        c = checks[d.name]
        if c.rejected:
            continue
        doc_known = KnownValues()
        if c.kind == "pdf":
            learn_pdf(d.content, policy, doc_known)
        elif c.kind == "image":
            learn_image(d.content, policy, doc_known)
        elif c.kind == "xlsx":
            learn_xlsx(d.content, policy, doc_known)
        identities[d.name] = DocIdentity.of(doc_known)
        known.merge(doc_known)
    same_patient = conflicts(identities)

    results = []
    for d in docs:  # pass 2: redact and verify
        c = checks[d.name]
        res = DocResult(name=d.name, kind=c.kind, status="pending", warnings=list(c.warnings),
                        pages=c.pages)  # fmt: skip
        res.identity = same_patient.get(d.name, {"dob": [], "name": []})
        if c.rejected:
            res.status, res.reject_code, res.message = "rejected", c.rejected, c.message
        elif c.kind in ("pdf", "image"):
            redact = redact_pdf if c.kind == "pdf" else redact_image
            detect = (
                (lambda page, img, name=d.name: ai_detect(name, page, img)) if ai_detect else None
            )
            out = redact(d.content, policy, known, extra_terms=d.extra_terms, keep=d.keep,
                         boxes=d.boxes, ai_detect=detect)  # fmt: skip
            ver = verify_pdf(out.output, policy, known, keep=d.keep, seeded=seeded,
                             extra_terms=d.extra_terms + out.ai_terms)  # fmt: skip
            res.output, res.output_name = out.output, output_name(d.name, c.kind)
            res.pages = out.pages
            res.warnings += out.notes
            if out.unverifiable_pages:
                res.warnings.append(f"OCR unavailable: pages {out.unverifiable_pages} unchecked")
            res.findings = [f.as_report(policy.requirement(f.category)) for f in out.findings]
            res.residuals = [f.as_report(policy.requirement(f.category)) for f in ver.residuals]
            res.verification = ver.summary()
            res.status = "verified" if ver.passed else "blocked"
        elif c.kind == "xlsx":
            out = redact_xlsx(d.content, policy, known, extra_terms=d.extra_terms, keep=d.keep)
            ver = verify_xlsx(out.output, policy, known, extra_terms=d.extra_terms, keep=d.keep)
            res.output, res.output_name = out.output, output_name(d.name, c.kind)
            res.findings = [f.as_report(policy.requirement(f.category)) for f in out.findings]
            res.residuals = [f.as_report(policy.requirement(f.category)) for f in ver.residuals]
            res.verification = ver.summary()
            res.status = "verified" if ver.passed else "blocked"
        else:
            res.message = f"No processor for {c.kind} files."
        results.append(res)
    return results
