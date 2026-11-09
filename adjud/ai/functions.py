"""Agent functions: name, versioned prompt file, output model and its JSON schema.

Each function's prompt is `prompts/<name>_v<version>.md`; changing a prompt means a new
version file, so recorded calls and replays stay tied to the prompt that produced them.
Output models carry no numeric constraints: structured outputs do not need them, and
the gates check ranges in code.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

PROMPTS = Path(__file__).parent / "prompts"


class Citation(BaseModel):
    """A short verbatim quote from one page of a redacted document."""

    model_config = ConfigDict(extra="forbid")
    file: str
    page: int
    quote: str


class DocumentTypeOutput(BaseModel):
    """AI-2: what kind of document this is."""

    model_config = ConfigDict(extra="forbid")
    document_type: str
    confidence: float
    evidence: Citation


class EventMatchOutput(BaseModel):
    """Do the case's documents describe this adverse event? (plan step 15)"""

    model_config = ConfigDict(extra="forbid")
    verdict: str  # matches | different_event | unclear (checked by the gate)
    confidence: float
    evidence: list[Citation]
    reason: str


class Statement(BaseModel):
    """One sentence of the narrative and the dossier page it comes from."""

    model_config = ConfigDict(extra="forbid")
    text: str
    page: int
    quote: str


class NarrativeOutput(BaseModel):
    """AI-4: a facts-only narrative, every statement cited (plan step 17)."""

    model_config = ConfigDict(extra="forbid")
    statements: list[Statement]


@dataclass(frozen=True)
class AgentFunction:
    name: str
    prompt_version: int
    output_model: type[BaseModel]

    @property
    def prompt_id(self) -> str:
        return f"{self.name}_v{self.prompt_version}"

    def system_prompt(self) -> str:
        return (PROMPTS / f"{self.prompt_id}.md").read_text(encoding="utf-8")

    def output_schema(self) -> dict[str, Any]:
        return inline_schema(self.output_model.model_json_schema())


def inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve `$defs` references and drop `title` keys: a plain, self-contained schema."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "title")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


DOCUMENT_TYPE = AgentFunction("document_type", 1, DocumentTypeOutput)
EVENT_MATCH = AgentFunction("event_match", 1, EventMatchOutput)
NARRATIVE = AgentFunction("narrative", 1, NarrativeOutput)
FUNCTIONS = {f.name: f for f in (DOCUMENT_TYPE, EVENT_MATCH, NARRATIVE)}
EVENT_VERDICTS = ("matches", "different_event", "unclear")
