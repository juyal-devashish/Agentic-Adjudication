"""PDF redaction, metadata scrub and verification (mvp-flow-1.md §7.5, §7.8).

Text pages: PyMuPDF redaction annotations over every finding's words, then
`apply_redactions`, which deletes the underlying characters (they are not just covered)
and blanks image pixels under the boxes. Then `scrub()` removes metadata, XMP, embedded
files, JavaScript, links and annotations, and the file is rewritten from scratch.

Pages that carry images (scans, image banners) are handled by OCR in step 5. Until then
such a page cannot be verified, so the file is marked blocked, never "verified".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pymupdf

from adjud.deid.detect import Finding, KnownValues, detect, learn, merge_spans
from adjud.deid.pagetext import TextUnit
from adjud.deid.policy import Policy

# Metadata keys that never hold identifiers (format/producer info).
_HARMLESS_META = {"format", "encryption", "producer", "creator", "creationDate", "modDate"}


def page_units(doc: pymupdf.Document) -> list[TextUnit]:
    return [
        TextUnit.from_words([w[:5] for w in page.get_text("words")], source=f"page:{i + 1}")
        for i, page in enumerate(doc)
    ]


def metadata_units(doc: pymupdf.Document) -> list[TextUnit]:
    units = [
        TextUnit.from_string(str(v), source=f"metadata:{k}")
        for k, v in (doc.metadata or {}).items()
        if v and k not in _HARMLESS_META
    ]
    xml = doc.get_xml_metadata()
    if xml:
        units.append(TextUnit.from_string(xml, source="metadata:xmp"))
    return units


def learn_pdf(content: bytes, policy: Policy, known: KnownValues) -> None:
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        for unit in page_units(doc) + metadata_units(doc):
            learn(unit, policy, known)


@dataclass
class PdfOutcome:
    output: bytes
    findings: list[Finding] = field(default_factory=list)
    image_pages: list[int] = field(default_factory=list)  # 1-based pages with images
    pages: int = 0


def redact_pdf(
    content: bytes, policy: Policy, known: KnownValues, *, extra_terms: list[str] | None = None,
    keep: list[tuple[str, int, int]] | None = None,
) -> PdfOutcome:  # fmt: skip
    """Redact every finding. `extra_terms` are site-added terms (F1-22). `keep` lists
    (source, start, end) findings the site unticked with a reason (F1-21)."""
    extra = KnownValues()
    for term in extra_terms or []:
        extra.values[term] = "SITE_TERM"
    keep_set = set(keep or [])
    outcome = PdfOutcome(output=b"")
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        outcome.pages = doc.page_count
        units = page_units(doc)
        meta_findings = [f for u in metadata_units(doc) for f in detect(u, policy, known)]
        for page, unit in zip(doc, units, strict=True):
            if page.get_images(full=True):
                outcome.image_pages.append(page.number + 1)
            findings = detect(unit, policy, known) + extra.find(unit)
            findings = [f for f in findings if (f.source, f.start, f.end) not in keep_set]
            outcome.findings += findings
            for s, e in merge_spans(findings):
                for rect in unit.rects_for(s, e):
                    page.add_redact_annot(pymupdf.Rect(rect), fill=(0, 0, 0))
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        outcome.findings += meta_findings  # metadata is removed whole, below
        doc.scrub()  # metadata, XMP, embedded files, JS, links, annotations, hidden text
        doc.set_metadata({})
        doc.del_xml_metadata()
        outcome.output = doc.tobytes(garbage=4, deflate=True, clean=True)
    return outcome


@dataclass
class PdfVerification:
    residuals: list[Finding] = field(default_factory=list)
    metadata_left: list[str] = field(default_factory=list)
    embedded_files: list[str] = field(default_factory=list)
    unverified_pages: list[int] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (
            self.residuals or self.metadata_left or self.embedded_files or self.unverified_pages
        )

    def summary(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "residuals": len(self.residuals),
            "metadata_left": self.metadata_left,
            "embedded_files": self.embedded_files,
            "unverified_pages": self.unverified_pages,
        }


def verify_pdf(
    output: bytes, policy: Policy, known: KnownValues, *, keep: list[str] | None = None,
    seeded: list[str] | None = None,
) -> PdfVerification:  # fmt: skip
    """Check the OUTPUT from scratch (§7.8): re-extract, re-run every detector, check
    metadata, and, for synthetic data, search for the generator's seeded identifiers.
    `keep` holds texts the site deliberately un-redacted; they do not count."""
    from adjud.deid.textmatch import contains

    result = PdfVerification()
    keep_texts = set(keep or [])
    with pymupdf.open(stream=output, filetype="pdf") as doc:
        for page, unit in zip(doc, page_units(doc), strict=True):
            hits = [f for f in detect(unit, policy, known) if f.text not in keep_texts]
            for value in seeded or []:
                if contains(unit.text, value):
                    hits.append(Finding(0, 0, "SEEDED", "seeded", value, source=unit.source))
            result.residuals += hits
            if page.get_images(full=True):
                result.unverified_pages.append(page.number + 1)
        result.metadata_left = [
            k for k, v in (doc.metadata or {}).items() if v and k not in _HARMLESS_META
        ]
        if doc.get_xml_metadata():
            result.metadata_left.append("xmp")
        result.embedded_files = list(doc.embfile_names())
    return result
