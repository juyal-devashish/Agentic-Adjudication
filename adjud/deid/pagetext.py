"""A unit of text (a PDF page, a spreadsheet cell, a metadata field) whose characters
can be mapped back to word boxes.

PDF pages are rebuilt as *visual* lines: words are grouped by vertical position, sorted
left to right, and a tab marks a wide horizontal gap. A two-column banner
("Patient:" at x=57, "ROBERTS, Philip" at x=176) therefore reads
"Patient:\tROBERTS, Philip", which is what the label rules need.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GAP = "\t"
Rect = tuple[float, float, float, float]


@dataclass
class TextUnit:
    text: str
    word_spans: list[tuple[int, int]] = field(default_factory=list)
    word_rects: list[Rect] = field(default_factory=list)
    source: str = ""  # e.g. "page:1", "metadata", "Results!B2"

    @classmethod
    def from_string(cls, text: str, source: str = "") -> TextUnit:
        return cls(text=text, source=source)

    @classmethod
    def from_words(
        cls, words: list[tuple[float, float, float, float, str]], *, gap: float = 12.0,
        source: str = "",
    ) -> TextUnit:  # fmt: skip
        """`words`: (x0, y0, x1, y1, text) as from PyMuPDF get_text("words")."""
        lines: list[list[tuple[float, float, float, float, str]]] = []
        centres: list[float] = []
        for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
            cy, h = (w[1] + w[3]) / 2, max(w[3] - w[1], 1.0)
            if lines and abs(cy - centres[-1]) <= 0.5 * h:
                lines[-1].append(w)
            else:
                lines.append([w])
                centres.append(cy)
        parts: list[str] = []
        spans: list[tuple[int, int]] = []
        rects: list[Rect] = []
        pos = 0
        for n, line in enumerate(lines):
            if n:
                parts.append("\n")
                pos += 1
            prev_x1 = None
            for w in sorted(line, key=lambda w: w[0]):
                if prev_x1 is not None:
                    sep = GAP if w[0] - prev_x1 > gap else " "
                    parts.append(sep)
                    pos += 1
                parts.append(w[4])
                spans.append((pos, pos + len(w[4])))
                rects.append((w[0], w[1], w[2], w[3]))
                pos += len(w[4])
                prev_x1 = w[2]
        return cls(text="".join(parts), word_spans=spans, word_rects=rects, source=source)

    def rects_for(self, start: int, end: int) -> list[Rect]:
        """Boxes of every word that overlaps [start, end). Whole words are redacted, so
        no fragment of an identifier can survive next to the box."""
        return [r for (s, e), r in zip(self.word_spans, self.word_rects, strict=True)
                if s < end and e > start]  # fmt: skip
