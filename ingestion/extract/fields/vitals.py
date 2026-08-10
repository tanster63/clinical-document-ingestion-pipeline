"""Wide vitals row, one per encounter.

A blank cell in the source table produces **no text block at all**, so column
position cannot be trusted: the provided chart fills only Ht, Wt, BMI and BSA,
and a positional reader slides those four values under BP, Pulse, Resp and O2.
Values are therefore paired to their label by horizontal overlap, which is
immune to however many cells were left empty. NULLs are the record of the gap
(§4.4), never a shifted neighbour.

One further wrinkle from the real export: a renderer may merge adjacent header
cells ("BMI BSA") and their values ("42.8 2.3"). A label cell that resolves to
several fields consumes that many numbers, in order.
"""

import re
from dataclasses import dataclass
from datetime import date

from ingestion.extract.layout import Block, text_of

LABEL_TO_FIELD: dict[str, str] = {
    "bp": "bp", "blood pressure": "bp",
    "pulse": "pulse", "hr": "pulse", "heart rate": "pulse",
    "resp": "respirations", "rr": "respirations", "respirations": "respirations",
    "o2 sat": "o2_sat", "spo2": "o2_sat", "o2": "o2_sat", "pulse ox": "o2_sat",
    "temp": "temperature_f", "temperature": "temperature_f",
    "ht": "height_in", "ht (in)": "height_in", "height": "height_in",
    "height (in)": "height_in",
    "wt": "weight_lbs", "wt (lbs)": "weight_lbs", "weight": "weight_lbs",
    "weight (lbs)": "weight_lbs",
    "bmi": "bmi", "bsa": "bsa",
    "date": "taken_date", "taken by": "taken_by",
}
NUMERIC_FIELDS = {"pulse", "respirations", "o2_sat", "temperature_f",
                  "height_in", "weight_lbs", "bmi", "bsa"}
INT_FIELDS = {"bp_systolic", "bp_diastolic", "pulse", "respirations", "o2_sat"}

NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
BP_RE = re.compile(r"(\d{2,3})\s*/\s*(\d{2,3})")
DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b")
INLINE_RE = {
    "bp": re.compile(r"\bBP\s*:?\s*(\d{2,3})\s*/\s*(\d{2,3})", re.IGNORECASE),
    "pulse": re.compile(r"\b(?:Pulse|HR)\s*:?\s*(\d{2,3})\b", re.IGNORECASE),
    "respirations": re.compile(r"\b(?:Resp|RR)\s*:?\s*(\d{1,2})\b", re.IGNORECASE),
    "o2_sat": re.compile(r"\b(?:O2\s*Sat|SpO2)\s*:?\s*(\d{2,3})\b", re.IGNORECASE),
    "temperature_f": re.compile(r"\bTemp\w*\s*:?\s*(\d{2,3}(?:\.\d)?)", re.IGNORECASE),
    "height_in": re.compile(r"\bHt\w*\s*:?\s*(\d{2,3}(?:\.\d)?)", re.IGNORECASE),
    "weight_lbs": re.compile(r"\bWt\w*\s*:?\s*(\d{2,3}(?:\.\d)?)", re.IGNORECASE),
    "bmi": re.compile(r"\bBMI\s*:?\s*(\d{2}(?:\.\d)?)", re.IGNORECASE),
    "bsa": re.compile(r"\bBSA\s*:?\s*(\d(?:\.\d)?)", re.IGNORECASE),
}
PATIENT_REPORTED_RE = re.compile(r"patient\s+reported", re.IGNORECASE)


@dataclass(frozen=True)
class VitalsFact:
    taken_by: str | None = None
    taken_date: date | None = None
    bp_systolic: int | None = None
    bp_diastolic: int | None = None
    pulse: int | None = None
    respirations: int | None = None
    o2_sat: int | None = None
    temperature_f: float | None = None
    height_in: float | None = None
    weight_lbs: float | None = None
    bmi: float | None = None
    bsa: float | None = None
    is_patient_reported: bool = False
    source_page: int | None = None


def _normalize(text: str) -> str:
    return " ".join(text.replace(".", "").replace(":", "").lower().split())


def _label_fields(text: str) -> list[str]:
    """The vitals fields a header cell names, or [] if it is not a header cell.

    A merged cell such as "BMI BSA" names two fields; a cell that mixes labels
    with values ("Ht 67.0 in") names none, and is left to the inline pass.
    """
    normalized = _normalize(text)
    if not normalized:
        return []
    whole = LABEL_TO_FIELD.get(normalized)
    if whole:
        return [whole]
    tokens = normalized.split()
    if len(tokens) < 2:
        return []
    fields = [LABEL_TO_FIELD.get(token) for token in tokens]
    return list(fields) if all(fields) else []


def _cells_below(label: Block, blocks: list[Block]) -> list[Block]:
    """Blocks under a label cell that overlap it horizontally, nearest first."""
    candidates = [
        b for b in blocks
        if b.y0 > label.y0 + 1 and min(b.x1, label.x1) - max(b.x0, label.x0) > 0
    ]
    return sorted(candidates, key=lambda b: b.y0 - label.y0)


def _as_date(text: str) -> date | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_vitals(blocks: list[Block]) -> VitalsFact | None:
    if not blocks:
        return None

    values: dict[str, float | int | str | date | None] = {}

    # Pass 1 — table geometry. Labels claim the value cell beneath them.
    for block in blocks:
        fields = _label_fields(block.text)
        if not fields:
            continue
        for candidate in _cells_below(block, blocks):
            raw = candidate.text.strip()
            if _label_fields(raw):
                break  # ran into the next header row; this column is empty
            if fields == ["taken_by"]:
                if raw and not NUMBER_RE.fullmatch(raw) and not _as_date(raw):
                    values.setdefault("taken_by", raw)
                    break
                continue
            if fields == ["taken_date"]:
                parsed = _as_date(raw)
                if parsed:
                    values.setdefault("taken_date", parsed)
                    break
                continue
            if fields == ["bp"]:
                bp = BP_RE.search(raw)
                if bp:
                    values.setdefault("bp_systolic", int(bp.group(1)))
                    values.setdefault("bp_diastolic", int(bp.group(2)))
                    break
                continue
            numbers = NUMBER_RE.findall(raw)
            if not numbers:
                continue
            for field, number in zip(fields, numbers):
                values.setdefault(field, float(number))
            break

    # Pass 2 — inline "Label value" text, for anything still missing.
    text = text_of(blocks)
    for field, pattern in INLINE_RE.items():
        target = "bp_systolic" if field == "bp" else field
        if values.get(target) is not None:
            continue
        match = pattern.search(text)
        if not match:
            continue
        if field == "bp":
            values["bp_systolic"], values["bp_diastolic"] = int(match.group(1)), int(match.group(2))
        else:
            values[field] = float(match.group(1))

    coerced = {
        key: (int(value) if key in INT_FIELDS and value is not None else value)
        for key, value in values.items()
    }
    return VitalsFact(
        **coerced,
        is_patient_reported=bool(PATIENT_REPORTED_RE.search(text)),
        source_page=blocks[0].page,
    )
