"""Pure helpers: turning detected text boxes into readable lines.

Kept free of Paddle imports so it can be tested without a 1 GB dependency.

PaddleOCR returns one entry per detected text box. On a page of notes a single
visual line is often several boxes (a formula, a gap, the rest of the sentence),
and the boxes are not guaranteed to come back left-to-right. Joining them in the
order they arrive produces text a person — or the LLM that extracts concepts
from it next — would misread. So boxes are grouped into lines by vertical
position first, then ordered left-to-right inside each line.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence

# Recognition score below which a line is flagged for the learner to check.
# Not a filter: nothing is dropped. Handwriting routinely scores 0.6–0.8 on
# text that is correct, and deleting it would lose real content.
LOW_CONFIDENCE = 0.80


@dataclass(frozen=True)
class Box:
    text: str
    score: float
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def y_center(self) -> float:
        return (self.y_min + self.y_max) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.y_max - self.y_min)


@dataclass(frozen=True)
class Line:
    text: str
    confidence: float  # lowest box score in the line: one bad word taints the line

    def as_dict(self) -> dict:
        return {"text": self.text, "confidence": round(self.confidence, 4)}


def box_from_polygon(text: str, score: float, polygon: Sequence[Sequence[float]]) -> Box:
    """A quadrilateral (4 points, any rotation) to its axis-aligned bounds."""
    xs = [float(p[0]) for p in polygon]
    ys = [float(p[1]) for p in polygon]
    return Box(text, float(score), min(xs), min(ys), max(xs), max(ys))


def group_lines(boxes: Sequence[Box]) -> list[Line]:
    """Group boxes into visual lines, top to bottom, each read left to right.

    Two boxes share a line when their vertical centres are closer than half the
    median box height. Median rather than mean: one large heading must not make
    every line on the page merge into its neighbour.
    """
    kept = [b for b in boxes if b.text.strip()]
    if not kept:
        return []
    tolerance = median(b.height for b in kept) / 2

    rows: list[list[Box]] = []
    for box in sorted(kept, key=lambda b: b.y_center):
        if rows and abs(box.y_center - _row_center(rows[-1])) <= tolerance:
            rows[-1].append(box)
        else:
            rows.append([box])

    lines = []
    for row in rows:
        ordered = sorted(row, key=lambda b: b.x_min)
        lines.append(
            Line(
                text=" ".join(b.text.strip() for b in ordered),
                confidence=min(b.score for b in ordered),
            )
        )
    return lines


def _row_center(row: Sequence[Box]) -> float:
    return sum(b.y_center for b in row) / len(row)


def page_summary(lines: Sequence[Line]) -> dict:
    """What the API reports for one page."""
    if not lines:
        return {"text": "", "lines": [], "mean_confidence": None, "low_confidence_count": 0}
    return {
        "text": "\n".join(l.text for l in lines),
        "lines": [l.as_dict() for l in lines],
        # None rather than 0 for an empty page: "no text found" and "text found
        # with zero confidence" must not look the same.
        "mean_confidence": round(sum(l.confidence for l in lines) / len(lines), 4),
        "low_confidence_count": sum(1 for l in lines if l.confidence < LOW_CONFIDENCE),
    }
