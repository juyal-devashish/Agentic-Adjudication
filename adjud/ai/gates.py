"""Code gates: may an agent's result move a case? (plan §2, step 12)

A gate passes only when the AI result is valid, its confidence is at or above the
study's threshold, every citation is found on the page it names, and every
deterministic check the caller adds has passed. Otherwise the reasons go to a person
as an exception task. Gates never call the API and never write anything.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from adjud.ai.gateway import AiResult

PageText = Callable[[str, int], str | None]  # (file, page) -> text, or None if no such page


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "reasons": self.reasons}


def _norm(text: str) -> str:
    """Lower case, one space between words, ignoring punctuation OCR tends to change."""
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def citation_found(quote: str, text: str | None) -> bool:
    q = _norm(quote)
    return text is not None and len(q) >= 4 and q in _norm(text)


def gate(
    result: AiResult,
    *,
    min_confidence: float,
    citations: Iterable[dict[str, Any]] = (),
    page_text: PageText | None = None,
    checks: Iterable[tuple[str, bool]] = (),
) -> GateResult:
    """`checks`: (reason if failed, passed) pairs from deterministic code."""
    reasons: list[str] = []
    if not result.ok or result.output is None:
        reasons.append(f"AI result not usable ({result.note or result.status})")
    else:
        conf = result.output.get("confidence")
        if conf is not None and not (0.0 <= float(conf) <= 1.0):
            reasons.append(f"confidence {conf} is not between 0 and 1")
        elif conf is not None and float(conf) < min_confidence:
            reasons.append(f"confidence {float(conf):.2f} is below {min_confidence:.2f}")
        for c in citations:
            text = page_text(c["file"], int(c["page"])) if page_text else None
            if not citation_found(str(c.get("quote", "")), text):
                reasons.append(f"quote not found on {c['file']} page {c['page']}")
    reasons += [reason for reason, passed in checks if not passed]
    return GateResult(passed=not reasons, reasons=reasons)
