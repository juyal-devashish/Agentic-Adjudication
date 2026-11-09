"""File type from content, not extension, and the upload checks.

Rejections tell the site exactly what to send instead; nothing is produced for them.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePath

import pymupdf

KIND_BY_EXTENSION = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".xlsx": "xlsx",
    ".dcm": "dicom",
}

REJECT_MESSAGES = {
    "ENCRYPTED": "The file is password-protected. Please send an unprotected copy.",
    "CORRUPT": "The file is damaged and cannot be opened. Please send it again.",
    "UNSUPPORTED_TYPE": "This file type is not accepted. Send PDF, PNG, JPG, TIFF or XLSX.",
    "TOO_LARGE": "The file is larger than the upload limit.",
    "TOO_MANY_PAGES": "The PDF has more pages than the upload limit.",
    "EMPTY": "The file is empty.",
}


def sniff(content: bytes) -> str:
    """pdf | image | xlsx | dicom | unknown, from the first bytes."""
    head = content[:140]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*")):
        return "image"
    if head[128:132] == b"DICM":
        return "dicom"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names = set(z.namelist())
            if "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names):
                return "xlsx"
        except zipfile.BadZipFile:
            return "unknown"
    return "unknown"


@dataclass
class Check:
    kind: str
    rejected: str | None = None  # a REJECT_MESSAGES code
    warnings: list[str] = field(default_factory=list)
    pages: int | None = None

    @property
    def message(self) -> str | None:
        return REJECT_MESSAGES.get(self.rejected) if self.rejected else None


def check_upload(
    name: str, content: bytes, *, max_file_mb: int = 50, max_pdf_pages: int = 200
) -> Check:
    """Validate one uploaded file (§7.2 step 2)."""
    if not content:
        return Check(kind="unknown", rejected="EMPTY")
    kind = sniff(content)
    check = Check(kind=kind)
    if len(content) > max_file_mb * 1024 * 1024:
        check.rejected = "TOO_LARGE"
        return check
    claimed = KIND_BY_EXTENSION.get(PurePath(name).suffix.lower())
    if kind == "unknown":
        # A file that claims to be a PDF but is not readable as one is damaged.
        check.rejected = "CORRUPT" if claimed in ("pdf", "xlsx") else "UNSUPPORTED_TYPE"
        return check
    if kind == "dicom":
        check.rejected = "UNSUPPORTED_TYPE"  # DICOM is stretch step B
        return check
    if claimed != kind:
        check.warnings.append(
            f"EXTENSION_MISMATCH: named {PurePath(name).suffix or '(none)'} but content is {kind}"
        )
    if kind == "pdf":
        try:
            with pymupdf.open(stream=content, filetype="pdf") as doc:
                if doc.needs_pass or doc.is_encrypted:
                    check.rejected = "ENCRYPTED"
                    return check
                check.pages = doc.page_count
                if doc.page_count == 0:
                    check.rejected = "CORRUPT"
                elif doc.page_count > max_pdf_pages:
                    check.rejected = "TOO_MANY_PAGES"
                else:
                    for page in doc:  # every page must render its text layer
                        page.get_text()
        except (RuntimeError, ValueError, pymupdf.FileDataError):
            check.rejected = "CORRUPT"
    return check
