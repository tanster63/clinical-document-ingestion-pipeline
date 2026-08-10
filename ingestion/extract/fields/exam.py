"""Structured physical-exam findings.

This is the largest and least regular block in an orthopedic chart, and the two
chart families print it differently again. The provided export lays it out as a
two-column table with typed sub-headings:

    Right Shoulder Active ROM:      Left Shoulder Active ROM:
    Forward Flexion: 180 degrees.   Forward Flexion: 180 degrees.

The rendered corpus writes a prose paragraph. Both are handled, and which one
was found is recorded in `finding_type` — a measurement carries a number and a
unit, a sentence of prose carries neither and is typed `narrative` rather than
being forced into a measurement shape it does not have.

Columns are recovered before anything else. Reading the export in plain reading
order interleaves the right and left columns, which would file every left-side
measurement under the right shoulder — the single worst error this parser could
make, because it silently reports the wrong side of the body.
"""

import re
from dataclasses import dataclass

from ingestion.extract.layout import Block, reading_order

FINDING_TYPES = {
    "active rom": "rom_active", "rom active": "rom_active", "rom": "rom_active",
    "passive rom": "rom_passive", "rom passive": "rom_passive",
    "strength": "strength", "special": "special_test", "special tests": "special_test",
    "stability": "stability", "inspection": "inspection", "skin": "skin",
    "palpation": "inspection", "neurovascular": "narrative",
}
NARRATIVE = "narrative"
BODY_PARTS = (
    "lumbar spine", "cervical spine", "thoracic spine", "shoulder", "elbow",
    "wrist", "hand", "finger", "thumb", "hip", "pelvis", "knee", "ankle",
    "foot", "toe", "spine", "back", "neck",
)
SIDES = ("right", "left", "bilateral")

HEADING_RE = re.compile(
    r"^\s*(?:(?P<side>right|left|bilateral)\s+)?"
    r"(?P<part>" + "|".join(BODY_PARTS) + r")?\s*"
    r"(?P<type>" + "|".join(FINDING_TYPES) + r")\s*:?\s*$",
    re.IGNORECASE,
)
PART_ONLY_RE = re.compile(
    r"^\s*(?:(?P<side>right|left|bilateral)\s+)?(?P<part>" + "|".join(BODY_PARTS) + r")\s*:?\s*$",
    re.IGNORECASE,
)
MEASURE_RE = re.compile(r"^\s*(?P<name>[A-Za-z][A-Za-z0-9 \-/'()]{0,48}?)\s*:\s*(?P<value>.+)$")
SIDE_PART_RE = re.compile(
    r"^\s*(?P<side>right|left|bilateral)\s+(?P<part>" + "|".join(BODY_PARTS) + r")\b",
    re.IGNORECASE,
)
VALUE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(degrees|deg|°|cm|mm|in|lbs|/5)?", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.;])\s+")
CONTINUATION_RE = re.compile(r"^[a-z(]")
# Two text columns are separated by a gap far wider than the indentation inside
# either of them. Expressed as a fraction of the section's own width so it holds
# at any page size.
COLUMN_GAP_FRACTION = 0.15


@dataclass(frozen=True)
class ExamFindingFact:
    body_part: str | None
    laterality: str | None
    finding_type: str
    measure_name: str | None
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    source_page: int | None


def _columns(blocks: list[Block]) -> list[list[Block]]:
    """Split one page's blocks into text columns by the gaps between their left
    edges, then return each column in top-to-bottom order."""
    if not blocks:
        return []
    span = max(b.x1 for b in blocks) - min(b.x0 for b in blocks)
    threshold = COLUMN_GAP_FRACTION * span
    by_left = sorted(blocks, key=lambda b: b.x0)
    columns: list[list[Block]] = [[by_left[0]]]
    for block in by_left[1:]:
        if block.x0 - columns[-1][-1].x0 > threshold:
            columns.append([])
        columns[-1].append(block)
    return [sorted(column, key=lambda b: b.y0) for column in columns]


def _measurement(value: str) -> tuple[float | None, str | None]:
    match = VALUE_RE.search(value)
    if not match:
        return None, None
    unit = match.group(2)
    return float(match.group(1)), unit.lower() if unit else None


def _emit(context: dict, measure: str | None, value: str, page: int) -> ExamFindingFact:
    side, part = context["side"], context["part"]
    inline = SIDE_PART_RE.match(measure or "")
    if inline:
        side, part = inline.group("side").lower(), inline.group("part").lower()
        measure = None
    numeric, unit = _measurement(value)
    return ExamFindingFact(
        body_part=part,
        laterality=side,
        finding_type=context["type"] if numeric is not None or measure else context["type"],
        measure_name=measure.strip() if measure else None,
        value_numeric=numeric,
        value_text=" ".join(value.split()).strip(" .") or None,
        unit=unit,
        source_page=page,
    )


def _parse_column(column: list[Block]) -> list[ExamFindingFact]:
    context = {"side": None, "part": None, "type": NARRATIVE}
    findings: list[ExamFindingFact] = []
    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        text = " ".join(pending)
        pending.clear()
        for sentence in SENTENCE_RE.split(text):
            sentence = sentence.strip()
            if sentence:
                findings.append(_emit(context, None, sentence, column[0].page))

    for block in column:
        line = block.text.strip()
        if not line:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            flush()
            context = {
                "side": (heading.group("side") or "").lower() or context["side"],
                "part": (heading.group("part") or "").lower() or context["part"],
                "type": FINDING_TYPES[heading.group("type").lower()],
            }
            continue
        part_only = PART_ONLY_RE.match(line)
        if part_only:
            flush()
            context = dict(context)
            context["part"] = part_only.group("part").lower()
            if part_only.group("side"):
                context["side"] = part_only.group("side").lower()
            continue
        if findings and CONTINUATION_RE.match(line) and not pending:
            previous = findings[-1]
            joined = f"{previous.value_text} {line}".strip(" .")
            findings[-1] = ExamFindingFact(
                **{**previous.__dict__, "value_text": " ".join(joined.split())}
            )
            continue
        measure = MEASURE_RE.match(line)
        if measure:
            flush()
            findings.append(
                _emit(context, measure.group("name"), measure.group("value"), block.page)
            )
            continue
        pending.append(line)
    flush()
    return findings


def parse_exam_findings(blocks: list[Block]) -> list[ExamFindingFact]:
    findings: list[ExamFindingFact] = []
    by_page: dict[int, list[Block]] = {}
    for block in reading_order(blocks):
        by_page.setdefault(block.page, []).append(block)
    for page_blocks in by_page.values():
        for column in _columns(page_blocks):
            findings.extend(_parse_column(column))
    return findings
