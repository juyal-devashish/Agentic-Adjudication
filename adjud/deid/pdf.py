"""PDF and image redaction, metadata scrub and verification (mvp-flow-1.md §7.3–7.5, §7.8).

Text-only pages: PyMuPDF redaction annotations over every finding's words, then
`apply_redactions`, which deletes the underlying characters (they are not just covered).

Pages with images (scans, image banners) and pages without a text layer: the page is
rendered at 300 dpi, OCRed, findings from OCR *and* from the text layer are blacked out,
and the page is rebuilt as an image-only page. No hidden text layer is kept.

Image files (PNG, JPG, TIFF) are made upright, straightened and output as PDF pages.

The output document is built fresh and scrubbed: no metadata, XMP, embedded files,
JavaScript, links or annotations.
"""

from __future__ import annotations

import hashlib
import io
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import pymupdf
from PIL import Image, ImageSequence

from adjud.deid import ocr
from adjud.deid.detect import Finding, KnownValues, detect, learn, merge_spans
from adjud.deid.pagetext import TextUnit
from adjud.deid.policy import Policy

# Metadata keys that never hold identifiers (format/producer info).
_HARMLESS_META = {"format", "encryption", "producer", "creator", "creationDate", "modDate"}
RENDER_DPI = 300


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


def needs_ocr(page: pymupdf.Page) -> bool:
    return bool(page.get_images(full=True)) or not page.get_text().strip()


def _render(page: pymupdf.Page) -> Image.Image:
    pix = page.get_pixmap(dpi=RENDER_DPI, colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


# --- OCR cache: learn and redact passes OCR the same pages once ------------------------------

_CACHE: OrderedDict[tuple[str, int], ocr.OcrPage] = OrderedDict()
_CACHE_SIZE = 24


def _cached(key: tuple[str, int], make) -> ocr.OcrPage:
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    value = make()
    _CACHE[key] = value
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return value


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def ocr_pdf_page(content: bytes, page: pymupdf.Page) -> ocr.OcrPage:
    # A page with a text layer keeps its geometry (no rotate/deskew) so text-layer boxes
    # still line up; a pure image page is made upright and straightened first.
    has_text = bool(page.get_text().strip())
    return _cached(
        (_digest(content), page.number),
        lambda: ocr.prepare(_render(page), RENDER_DPI, upright=not has_text),
    )


def image_frames(content: bytes) -> list[tuple[Image.Image, float | None]]:
    with Image.open(io.BytesIO(content)) as img:
        dpi = (img.info.get("dpi") or (None,))[0]
        return [(frame.convert("L"), dpi) for frame in ImageSequence.Iterator(img)]


def ocr_image_frame(content: bytes, n: int, frame: Image.Image, dpi: float | None) -> ocr.OcrPage:
    return _cached((_digest(content), n), lambda: ocr.prepare(frame, dpi, upright=True))


# --- pass 1: learn ---------------------------------------------------------------------------


def learn_pdf(content: bytes, policy: Policy, known: KnownValues) -> None:
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        for page, unit in zip(doc, page_units(doc), strict=True):
            learn(unit, policy, known)
            if needs_ocr(page) and ocr.available():
                learn(ocr_pdf_page(content, page).unit, policy, known)
        for unit in metadata_units(doc):
            learn(unit, policy, known)


def learn_image(content: bytes, policy: Policy, known: KnownValues) -> None:
    if not ocr.available():
        return
    for n, (frame, dpi) in enumerate(image_frames(content)):
        learn(ocr_image_frame(content, n, frame, dpi).unit, policy, known)


# --- pass 2: redact --------------------------------------------------------------------------


@dataclass
class PdfOutcome:
    output: bytes
    findings: list[Finding] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)  # 1-based pages rebuilt from OCR
    unverifiable_pages: list[int] = field(default_factory=list)  # images, but no OCR
    notes: list[str] = field(default_factory=list)
    pages: int = 0


def _findings(
    unit: TextUnit, policy: Policy, known: KnownValues, extra: KnownValues, keep: set[str]
) -> list[Finding]:
    found = detect(unit, policy, known) + extra.find(unit)
    return [f for f in found if f.text not in keep]


def _extra(terms: list[str] | None) -> KnownValues:
    extra = KnownValues()
    for term in terms or []:
        if term.strip():
            extra.values[term.strip()] = "SITE_TERM"
    return extra


def _image_page(out: pymupdf.Document, img: Image.Image, dpi: float) -> None:
    page = out.new_page(width=img.width * 72 / dpi, height=img.height * 72 / dpi)
    page.insert_image(page.rect, stream=ocr.png_bytes(img))


def _finish(out: pymupdf.Document) -> bytes:
    out.scrub()  # metadata, XMP, embedded files, JS, links, annotations, hidden text
    out.set_metadata({})
    out.del_xml_metadata()
    return out.tobytes(garbage=4, deflate=True, clean=True)


def redact_pdf(
    content: bytes,
    policy: Policy,
    known: KnownValues,
    *,
    extra_terms: list[str] | None = None,
    keep: list[str] | None = None,
) -> PdfOutcome:
    """Redact every finding. `extra_terms` are site-added terms (F1-22); `keep` holds texts
    the site unticked with a reason (F1-21)."""
    extra, keep_set = _extra(extra_terms), set(keep or [])
    outcome = PdfOutcome(output=b"")
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        outcome.pages = doc.page_count
        units = page_units(doc)
        outcome.findings += [f for u in metadata_units(doc) for f in detect(u, policy, known)]
        rebuilt: dict[int, tuple[Image.Image, float]] = {}
        for page, unit in zip(doc, units, strict=True):
            text_findings = _findings(unit, policy, known, extra, keep_set)
            outcome.findings += text_findings
            if needs_ocr(page) and ocr.available():
                op = ocr_pdf_page(content, page)
                img_findings = _findings(op.unit, policy, known, extra, keep_set)
                outcome.findings += img_findings
                rects = [r for s, e in merge_spans(img_findings) for r in op.unit.rects_for(s, e)]
                scale = op.dpi / 72  # text-layer boxes -> pixels (geometry kept, see above)
                rects += [
                    (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
                    for s, e in merge_spans(text_findings)
                    for x0, y0, x1, y1 in unit.rects_for(s, e)
                ]
                rebuilt[page.number] = (ocr.black_out(op.image, rects), op.dpi)
                outcome.ocr_pages.append(page.number + 1)
                outcome.notes += [f"page {page.number + 1}: {n}" for n in op.notes]
                continue
            if needs_ocr(page):
                outcome.unverifiable_pages.append(page.number + 1)
            for s, e in merge_spans(text_findings):
                for rect in unit.rects_for(s, e):
                    page.add_redact_annot(pymupdf.Rect(rect), fill=(0, 0, 0))
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        with pymupdf.open() as out:
            for page in doc:
                if page.number in rebuilt:
                    _image_page(out, *rebuilt[page.number])
                else:
                    out.insert_pdf(doc, from_page=page.number, to_page=page.number)
            outcome.output = _finish(out)
    return outcome


def redact_image(
    content: bytes,
    policy: Policy,
    known: KnownValues,
    *,
    extra_terms: list[str] | None = None,
    keep: list[str] | None = None,
) -> PdfOutcome:
    """PNG/JPG/TIFF -> upright, straightened, redacted image-only PDF (one page per frame)."""
    extra, keep_set = _extra(extra_terms), set(keep or [])
    outcome = PdfOutcome(output=b"")
    frames = image_frames(content)
    outcome.pages = len(frames)
    with pymupdf.open() as out:
        for n, (frame, dpi) in enumerate(frames):
            if not ocr.available():
                outcome.unverifiable_pages.append(n + 1)
                _image_page(out, frame, float(dpi or RENDER_DPI))
                continue
            op = ocr_image_frame(content, n, frame, dpi)
            findings = _findings(op.unit, policy, known, extra, keep_set)
            for f in findings:
                f.source = f"page:{n + 1}"
            outcome.findings += findings
            rects = [r for s, e in merge_spans(findings) for r in op.unit.rects_for(s, e)]
            _image_page(out, ocr.black_out(op.image, rects), op.dpi)
            outcome.ocr_pages.append(n + 1)
            outcome.notes += [f"page {n + 1}: {note}" for note in op.notes]
        outcome.output = _finish(out)
    return outcome


# --- verification ---------------------------------------------------------------------------


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
    output: bytes,
    policy: Policy,
    known: KnownValues,
    *,
    keep: list[str] | None = None,
    seeded: list[str] | None = None,
    extra_terms: list[str] | None = None,
) -> PdfVerification:
    """Check the OUTPUT from scratch (§7.8): re-extract text (and fresh OCR of every image
    page), re-run every detector, check metadata, and, for synthetic data, search for the
    generator's seeded identifiers. Texts the site deliberately kept do not count."""
    from adjud.deid.textmatch import contains

    result = PdfVerification()
    extra, keep_set = _extra(extra_terms), set(keep or [])
    with pymupdf.open(stream=output, filetype="pdf") as doc:
        for page, unit in zip(doc, page_units(doc), strict=True):
            units = [unit]
            if needs_ocr(page):
                if ocr.available():
                    units.append(ocr.ocr_unit(_render(page), source=unit.source))
                else:
                    result.unverified_pages.append(page.number + 1)
            for u in units:
                result.residuals += _findings(u, policy, known, extra, keep_set)
                for value in seeded or []:
                    if value not in keep_set and contains(u.text, value):
                        result.residuals.append(
                            Finding(0, 0, "SEEDED", "seeded", value, source=unit.source)
                        )
        result.metadata_left = [
            k for k, v in (doc.metadata or {}).items() if v and k not in _HARMLESS_META
        ]
        if doc.get_xml_metadata():
            result.metadata_left.append("xmp")
        result.embedded_files = list(doc.embfile_names())
    return result
