"""Lines of text that remember where each word sits on the page.

Positions let the extractor read table-style layouts by COLUMN (a value under its
heading), which plain text cannot do. Plain text (e.g. in unit tests) is supported
too: each character is then treated as one fixed-width unit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

NO_SPACE_BEFORE = {",", ".", "%", ":", ";", ")"}


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float = 0.0
    bottom: float = 0.0


@dataclass
class Line:
    words: list[Word]
    page: int = 0
    real_layout: bool = True
    text: str = ""
    spans: list[tuple[int, int]] = field(default_factory=list)

    def __post_init__(self):
        parts, spans, pos = [], [], 0
        for k, w in enumerate(self.words):
            sep = "" if k == 0 or w.text in NO_SPACE_BEFORE else " "
            pos += len(sep)
            spans.append((pos, pos + len(w.text)))
            parts.append(sep + w.text); pos += len(w.text)
        self.text = "".join(parts); self.spans = spans

    @property
    def top(self): return min((w.top for w in self.words), default=0)

    def words_in(self, a: int, b: int) -> list[Word]:
        """Words overlapping character range [a, b)."""
        return [w for w, (s, e) in zip(self.words, self.spans) if s < b and e > a]

    def xspan(self, a: int, b: int):
        ws = self.words_in(a, b)
        return (min(w.x0 for w in ws), max(w.x1 for w in ws)) if ws else None


def lines_from_text(text: str) -> list[Line]:
    """Plain text -> lines with pseudo positions (1 unit per character)."""
    out = []
    for raw in text.replace(" ", " ").splitlines():
        if not raw.strip():
            continue
        words, x = [], 0
        for tok in raw.split(" "):
            if tok:
                words.append(Word(tok, x, x + len(tok)))
            x += len(tok) + 1
        out.append(Line(words, real_layout=False))
    return out


def group_into_lines(words: list[Word], page: int) -> list[Line]:
    """Cluster positioned words into visual lines (vertical overlap), left to right."""
    words = sorted(words, key=lambda w: (w.top, w.x0))
    rows: list[list[Word]] = []
    for w in words:
        h = max(w.bottom - w.top, 1.0)
        placed = False
        for row in reversed(rows[-3:]):
            rt, rb = min(x.top for x in row), max(x.bottom for x in row)
            overlap = min(rb, w.bottom) - max(rt, w.top)
            if overlap > 0.5 * min(h, rb - rt):
                row.append(w); placed = True; break
        if not placed:
            rows.append([w])
    out = []
    for row in rows:
        row.sort(key=lambda w: w.x0)
        merged: list[Word] = []
        for w in row:  # re-join a lone comma etc. produced by some PDF writers
            if merged and w.text in NO_SPACE_BEFORE and w.x0 - merged[-1].x1 < 4:
                m = merged[-1]; merged[-1] = Word(m.text + w.text, m.x0, w.x1, m.top, max(m.bottom, w.bottom))
            else:
                merged.append(w)
        out.append(Line(merged, page))
    return sorted(out, key=lambda l: l.top)
