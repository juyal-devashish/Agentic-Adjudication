"""Files a site attaches in its conversation with the Agent (plan P-58, Q8, Q13).

In the demo, with synthetic data only, a site may send ORIGINAL documents by attaching
them in Slack. They are fetched with the bot token (`files:read`) and added to the case
by the Agent's upload tool, with the same call as the Documents page. The token goes
only to Slack's own file host: a file that is external (a link to another service) or
whose download URL is anywhere but `https://files.slack.com` is refused before any
request, and redirects are not followed, so the token never leaves Slack. The declared
size is checked before the download and the bytes read are capped, and the content must
be a type the app accepts (`adjud.deid.sniff.check_upload`; Slack's sign-in page, served
instead of a file the bot cannot read, is not one).

Afterwards the app asks Slack to delete the file; a bot may delete only its own files,
so for a person's attachment Slack usually refuses and the original stays in Slack. The
outcome is recorded either way and never fails the upload. Bytes are kept in memory
only; neither file names nor the token are logged.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any
from urllib.parse import urlparse

from adjud.deid.sniff import KIND_BY_EXTENSION, REJECT_MESSAGES, check_upload

SLACK_FILE_HOST = "files.slack.com"
TIMEOUT_SECONDS = 30
# GET(url, headers, at most this many bytes) -> the bytes; tests pass a fake
Opener = Callable[[str, dict[str, str], int], bytes]


class FileRefused(Exception):
    """One attached file is not added; the message says why, in words for the site."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a redirect is an error: the Authorization header never follows it


def https_get(url: str, headers: dict[str, str], limit: int) -> bytes:
    """GET without following redirects, reading at most `limit` bytes."""
    if urlparse(url).scheme != "https":
        raise ValueError("https only")
    request = urllib.request.Request(url, method="GET")  # noqa: S310 (https only, above)
    for k, v in headers.items():
        request.add_unredirected_header(k, v)
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read(limit)


@dataclass
class SlackFiles:
    """Fetches and deletes attached files through one Slack client (slack_sdk's
    WebClient: `token`, `files_info`, `files_delete`)."""

    client: Any
    max_file_mb: int = 50
    max_pdf_pages: int = 200
    opener: Opener = field(default=https_get)

    def describe(self, f: dict[str, Any]) -> dict[str, Any]:
        """The full file object: a message may carry only its id, or ask the app to look
        it up (`file_access: check_file_info`)."""
        if (
            f.get("url_private_download")
            and f.get("mode")
            and (f.get("file_access") != "check_file_info")
        ):
            return f
        try:
            resp = self.client.files_info(file=f["id"])
        except Exception as exc:
            why = f"Slack did not give the app the file ({type(exc).__name__})"
            raise FileRefused(why) from None
        return dict(resp.get("file") or {})

    def fetch(self, f: dict[str, Any]) -> tuple[str, bytes]:
        """(name, content) of one attached file, or FileRefused before any request when it
        is not a file the app takes."""
        f = self.describe(f)
        name = str(f.get("name") or f.get("title") or "attachment")
        if f.get("is_external") or f.get("mode") != "hosted":
            raise FileRefused("it is a link to a file elsewhere, not a file in Slack")
        url = str(f.get("url_private_download") or "")
        parts = urlparse(url)
        if parts.scheme != "https" or parts.hostname != SLACK_FILE_HOST:
            raise FileRefused("Slack gave no download address for it")
        if PurePath(name).suffix.lower() not in KIND_BY_EXTENSION:
            raise FileRefused(REJECT_MESSAGES["UNSUPPORTED_TYPE"])
        limit = self.max_file_mb * 1024 * 1024
        if int(f.get("size") or 0) > limit:
            raise FileRefused(f"it is larger than the upload limit of {self.max_file_mb} MB")
        token = str(getattr(self.client, "token", "") or "")
        try:
            content = self.opener(url, {"Authorization": f"Bearer {token}"}, limit + 1)
        except Exception as exc:  # by type only: an error's text may carry the URL
            raise FileRefused(f"downloading it from Slack failed ({type(exc).__name__})") from None
        check = check_upload(name, content, max_file_mb=self.max_file_mb,
                             max_pdf_pages=self.max_pdf_pages)  # fmt: skip
        if check.rejected:
            raise FileRefused(check.message or "it cannot be used")
        return name, content

    def delete(self, file_id: str) -> str:
        """Ask Slack to delete the file: "deleted", or Slack's error word (e.g.
        cant_delete_file: the original stays in Slack). Never raises."""
        try:
            self.client.files_delete(file=file_id)
            return "deleted"
        except Exception as exc:
            resp = getattr(exc, "response", None)
            word = str(resp.get("error", "")) if hasattr(resp, "get") else ""
            return (
                word
                if word.replace("_", "").isalpha() and len(word) <= 40
                else (type(exc).__name__)
            )
