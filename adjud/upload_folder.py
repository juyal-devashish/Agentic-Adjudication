"""Per-case upload folders (plan step 19, P-31).

The runner keeps one folder per case that is waiting for documents, named after the case
number (`uploads/<case-id>/`, or under ADJUD_UPLOAD_DIR). The site puts **one ZIP** with
all its documents in it. A ZIP is imported once it is complete: its size and time stamp
did not change since the previous tick, and it opens as a valid archive. That is code,
not AI, deciding that the upload is finished. Its files then go through exactly the
same `upload()` as the UI, so de-identification starts on its own.

A ZIP is refused, with a reason, a notification and an `upload.rejected` event, when it
is corrupt, encrypted, holds another archive, has paths outside the folder, or unpacks
to too many files or bytes. Imported and refused ZIPs move to `imported/` and `rejected/`
inside the case's folder; nothing is deleted.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from adjud.documents import upload
from adjud.events import Clock, EventDraft, sha256_bytes, utc_now
from adjud.notify import Outbox
from adjud.store import FileCaseStore
from adjud.workflow import TRANSITIONS, State

log = logging.getLogger("adjud.uploads")
ACTOR = "system:upload-folder"
MAX_FILES = 50
MAX_UNPACKED = 300 * 1024 * 1024
SKIP = ("__MACOSX/", ".DS_Store", "Thumbs.db")
ARCHIVES = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz")
WAITING = (State.AWAITING_DOCUMENTS, State.QUERY_OPEN)  # folders exist for these states
README = """Upload folder for case {case}

Put ONE ZIP file here with all the documents for this case (PDF, scans, spreadsheets).
It is picked up automatically within a minute. Do not put patient names in file names.
Synthetic demo data only.
"""


class ZipRefused(Exception):
    pass


def unpack(content: bytes) -> list[tuple[str, bytes]]:
    """The documents in a ZIP, or ZipRefused with a reason the site can act on. The
    listing is checked before anything is read: encryption, paths, nested archives and
    size limits; then every member is read and its checksum verified."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, EOFError, ValueError) as exc:
        raise ZipRefused("The ZIP file is damaged. Create it again and upload it.") from exc
    members, total = [], 0
    for info in zf.infolist():
        name = info.filename
        if info.is_dir() or name.startswith(SKIP) or PurePosixPath(name).name in SKIP:
            continue
        if info.flag_bits & 0x1:
            raise ZipRefused("The ZIP file is password-protected. Send it without a password.")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ZipRefused(f"The ZIP file has an unsafe path ({name}). Put the files at the "
                             "top level and create it again.")  # fmt: skip
        if path.suffix.lower() in ARCHIVES:
            raise ZipRefused(f"The ZIP file holds another archive ({path.name}). Put the "
                             "documents in directly.")  # fmt: skip
        total += info.file_size
        if len(members) >= MAX_FILES or total > MAX_UNPACKED:
            raise ZipRefused(f"The ZIP file is too large: at most {MAX_FILES} files and "
                             f"{MAX_UNPACKED // 2**20} MB unpacked.")  # fmt: skip
        members.append((path.name, info))
    if not members:
        raise ZipRefused("The ZIP file is empty.")
    try:
        return [(name, zf.read(info)) for name, info in members]  # read() checks the CRC
    except (zipfile.BadZipFile, EOFError, RuntimeError, ValueError, OSError) as exc:
        raise ZipRefused("The ZIP file is damaged. Create it again and upload it.") from exc


@dataclass
class UploadFolders:
    store: FileCaseStore
    outbox: Outbox
    clock: Clock = field(default=utc_now)
    seen: dict[Path, tuple[int, int]] = field(default_factory=dict)  # zip -> (size, mtime)

    @property
    def root(self) -> Path:
        return self.store.paths.uploads

    def ensure_folders(self) -> None:
        for state in WAITING:
            for cs in self.store.list_case_states(state):
                folder = self.root / cs.case_id
                if not folder.exists():
                    folder.mkdir(parents=True)
                    (folder / "HOW_TO_UPLOAD.txt").write_text(README.format(case=cs.case_id))

    def tick(self) -> dict[str, list[str]]:
        """Create missing folders, then import every complete ZIP whose case can take it."""
        self.ensure_folders()
        done: dict[str, list[str]] = {"imported": [], "rejected": []}
        if not self.root.exists():
            return done
        allowed = TRANSITIONS["documents.uploaded"]
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if not (self.store.case_dir(folder.name) / "events.jsonl").exists():
                continue
            for z in sorted(folder.glob("*.zip")):
                stat = z.stat()
                now = (stat.st_size, stat.st_mtime_ns)
                if self.seen.get(z) != now:  # new or still being written: look again next tick
                    self.seen[z] = now
                    continue
                if self.store.state(folder.name).state not in allowed:
                    continue  # e.g. already signed: wait for a query before taking files
                self.seen.pop(z, None)
                done["imported" if self._import(folder.name, z) else "rejected"].append(z.name)
        return done

    def _import(self, case_id: str, z: Path) -> bool:
        content = z.read_bytes()
        via: dict[str, Any] = {"zip": z.name, "sha256": sha256_bytes(content)}
        stamp = self.clock().strftime("%Y%m%dT%H%M%S")
        try:
            files = unpack(content)
        except ZipRefused as exc:
            self._move(z, "rejected", stamp)
            (z.parent / "rejected" / f"{stamp}-{z.name}.reason.txt").write_text(f"{exc}\n")
            self.store.append_event(case_id, EventDraft(
                type="upload.rejected", actor=ACTOR, data={**via, "reason": str(exc)}))  # fmt: skip
            self._notify(case_id, "upload_rejected")
            log.warning("ZIP %s for %s refused: %s", z.name, case_id, exc)
            return False
        upload(self.store, case_id, files, ACTOR, via=via)
        self._move(z, "imported", stamp)
        log.info("ZIP %s for %s imported: %d files", z.name, case_id, len(files))
        return True

    def _move(self, z: Path, to: str, stamp: str) -> None:
        target = z.parent / to
        target.mkdir(exist_ok=True)
        z.rename(target / f"{stamp}-{z.name}")

    def _notify(self, case_id: str, template: str) -> None:
        subject = self.store.state(case_id).subject or "--"
        to = f"site:{subject.split('-')[1]}"
        note = self.outbox.queue(case_id, to, template)
        self.store.append_event(case_id, EventDraft(
            type="notification.queued", actor=ACTOR,
            data={"to": to, "template": template, "id": note["id"]}))  # fmt: skip
