"""Local tools that agents call (plan step 12, "AI triggering local tools").

Every tool is read-only and sees only **redacted** outputs and EDC snapshots, never
`documents/original/`. No tool writes a file or an event: an agent's result is a
proposal, and only the runner moves a case after a gate passes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pymupdf

from adjud.deid import ocr
from adjud.documents import documents_of
from adjud.store import FileCaseStore

MAX_PAGE_CHARS = 12_000  # one page of a clinical letter is far below this


class ToolError(Exception):
    """A tool could not answer; the agent gets the message as an error result."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], Any]

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


@dataclass
class Toolbox:
    tools: list[Tool] = field(default_factory=list)

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in self.tools]

    def call(self, name: str, args: dict[str, Any]) -> str:
        """Run a tool; the result is JSON text. Raises ToolError for the agent to see."""
        tool = next((t for t in self.tools if t.name == name), None)
        if tool is None:
            raise ToolError(f"unknown tool {name!r}")
        return json.dumps(tool.run(args), ensure_ascii=False)


def _schema(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def redacted_outputs(store: FileCaseStore, case_id: str) -> dict[str, dict[str, Any]]:
    """Original file name -> the last run's processed entry, for files with an output."""
    return {
        name: d for name, d in documents_of(store, case_id).processed.items() if d.get("output")
    }


def page_text(store: FileCaseStore, case_id: str, file: str, page: int) -> str | None:
    """Text of one page of a redacted PDF output: the text layer, or OCR for image-only
    pages. None if there is no such file or page. Used by tools and by the gates."""
    out = redacted_outputs(store, case_id).get(file)
    if out is None or not out["output"]["path"].endswith(".pdf"):
        return None
    content = store.get_file(case_id, out["output"]["path"])
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        if not 1 <= page <= doc.page_count:
            return None
        p = doc[page - 1]
        text = p.get_text()
        if not text.strip() and ocr.available():
            pix = p.get_pixmap(dpi=ocr.TARGET_DPI, colorspace=pymupdf.csGRAY)
            from PIL import Image

            img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            text = ocr.ocr_unit(img, ocr.TARGET_DPI).text
    return text[:MAX_PAGE_CHARS]


def case_toolbox(store: FileCaseStore, case_id: str) -> Toolbox:
    """The tools an agent may use on one case."""

    def list_documents(_: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"file": name, "status": d["status"], "pages": d.get("pages")}
            for name, d in sorted(redacted_outputs(store, case_id).items())
        ]

    def read_page_text(args: dict[str, Any]) -> dict[str, Any]:
        text = page_text(store, case_id, str(args["file"]), int(args["page"]))
        if text is None:
            raise ToolError(f"no page {args['page']} in a redacted file named {args['file']!r}")
        return {"file": args["file"], "page": args["page"], "text": text}

    def get_ae_record(_: dict[str, Any]) -> dict[str, Any]:
        snapshot = None
        for e in store.events(case_id):
            if e["type"] in ("case.created", "source.changed"):
                snapshot = e["data"]["snapshot"]["path"]
        if snapshot is None:
            raise ToolError("this case has no AE snapshot")
        return store.get_json(case_id, snapshot)

    return Toolbox(
        [
            Tool(
                "list_documents",
                "List the case's de-identified documents with their page counts.",
                _schema({}),
                list_documents,
            ),
            Tool(
                "read_page_text",
                "Read the text of one page of a de-identified document (identifiers are "
                "already blacked out). Pages are numbered from 1.",
                _schema(
                    {
                        "file": {"type": "string", "description": "File name as listed"},
                        "page": {"type": "integer", "description": "Page number, from 1"},
                    }
                ),
                read_page_text,
            ),
            Tool(
                "get_ae_record",
                "Get the adverse event record from the EDC for this case (pseudonymised: "
                "subject ID, event term, coded terms, dates, seriousness, outcome).",
                _schema({}),
                get_ae_record,
            ),
        ]
    )
