"""Prescriptions written at an encounter.

The section text is segmented on the Quantity/Refills tail that every
prescription ends with. That gives an unambiguous boundary between adjacent
prescriptions without relying on line breaks surviving PDF extraction, and it
works whether the prescription sits under its own heading (rendered corpus) or
inline under "Plan: Prescription." (provided export).

Nothing here is inferred. A hallucinated refill count or sig is unacceptable in
a clinical record, so every field is read from the page or left NULL.
"""

import re
from dataclasses import dataclass

from ingestion.extract.layout import Block, text_of

TAIL_RE = re.compile(
    r"Quantity\s*:?\s*(?P<qty>\d+(?:\.\d+)?)\s*(?P<qty_unit>[A-Za-z]+)?"
    r"\s*Refills\s*:?\s*(?P<refills>\d+)"
    r"(?:\s*Action\s*:?\s*(?P<action>new|modify|continue))?",
    re.IGNORECASE,
)
SIG_RE = re.compile(r"\bSig\s*:?\s*", re.IGNORECASE)
DOSE_FORMS = (
    "tablet", "tab", "capsule", "cap", "solution", "suspension", "cream", "gel",
    "ointment", "patch", "injection", "inhaler", "spray", "suppository",
)
ROUTES = ("PO", "Oral", "IM", "IV", "SC", "SL", "topical", "PR", "inhaled")
DRUG_RE = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z0-9\-]*(?:\s+[A-Za-z][A-Za-z0-9\-]*)*?)"
    r"\s+(?P<strength>\d+(?:\.\d+)?)\s*(?P<unit>mg/mL|mcg|mg|g|mL|%|units?)"
    r"(?:\s+(?P<form>" + "|".join(DOSE_FORMS) + r"))?"
    r"(?:\s*[—–-]\s*|\s+)?(?P<route>" + "|".join(ROUTES) + r")?",
    re.IGNORECASE,
)
DURATION_RE = re.compile(
    r"(?:x|for)\s*(?P<n>\d+)\s*(?P<unit>day|days|week|weeks|month|months)", re.IGNORECASE
)
PRN_RE = re.compile(r"\bPRN\b", re.IGNORECASE)
UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30}


@dataclass(frozen=True)
class PrescriptionFact:
    drug_name: str
    strength: str | None
    strength_unit: str | None
    dose_form: str | None
    route: str | None
    sig_text: str
    quantity: float | None
    quantity_unit: str | None
    refills: int | None
    duration_days: int | None
    is_prn: bool
    action: str
    source_page: int | None


def parse_duration_days(sig: str) -> int | None:
    match = DURATION_RE.search(sig)
    if not match:
        return None
    return int(match.group("n")) * UNIT_DAYS[match.group("unit").lower()]


def _segments(text: str) -> list[tuple[str, int, re.Match[str]]]:
    """Split the section into (segment_text, tail offset within it, tail match).

    The offset is returned because the match's own indices address the whole
    section, not the slice. Mixing the two silently swallows the next
    prescription's Quantity/Refills line into the previous one's sig — and only
    from the second prescription onwards, which is exactly the kind of bug that
    passes a single-prescription test.
    """
    out, start = [], 0
    for tail in TAIL_RE.finditer(text):
        out.append((text[start:tail.end()], tail.start() - start, tail))
        start = tail.end()
    return out


def parse_prescriptions(blocks: list[Block]) -> list[PrescriptionFact]:
    if not blocks:
        return []
    text = text_of(blocks)
    page = blocks[0].page
    facts: list[PrescriptionFact] = []

    for segment, tail_offset, tail in _segments(text):
        sig_match = SIG_RE.search(segment)
        if not sig_match:
            continue
        drug_line = segment[: sig_match.start()].strip()
        sig_text = segment[sig_match.end(): tail_offset].strip(" ,;-")

        drug = DRUG_RE.search(drug_line)
        if not drug:
            continue
        quantity = tail.group("qty")

        facts.append(PrescriptionFact(
            drug_name=drug.group("name").strip().lower(),
            strength=drug.group("strength"),
            strength_unit=drug.group("unit"),
            dose_form=drug.group("form").lower() if drug.group("form") else None,
            route=drug.group("route") if drug.group("route") else None,
            sig_text=sig_text,
            quantity=float(quantity) if quantity else None,
            quantity_unit=tail.group("qty_unit"),
            refills=int(tail.group("refills")),
            duration_days=parse_duration_days(sig_text),
            is_prn=bool(PRN_RE.search(sig_text)),
            action=(tail.group("action") or "new").lower(),
            source_page=page,
        ))
    return facts
