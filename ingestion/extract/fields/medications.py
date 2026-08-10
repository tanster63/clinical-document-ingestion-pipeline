"""Medications the patient was already taking, read from the sidebar snapshot.

This is deliberately a different fact from a prescription written at the visit
(§4.3). The sidebar is what the patient was on *when they walked in*; it is
valid only as of that encounter, which is why it is stored per encounter rather
than per patient. In the provided chart meloxicam is absent in July and present
in August, because it was prescribed in between — a patient-level medication
list would erase exactly that.
"""

import re
from dataclasses import dataclass

from ingestion.extract.layout import Block, reading_order
from ingestion.extract.sections import find_sections

ROUTE_RE = re.compile(r"\b(Oral|PO|Topical|Injection|Inhaled|SL|IM|IV|PR)\b", re.IGNORECASE)
STRENGTH_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg/mL|mcg|mg|g|mL|%|units?)\b", re.IGNORECASE)
DOSE_FORM_RE = re.compile(
    r"\b(?:tablet|tab|capsule|cap|solution|suspension|cream|gel|ointment|patch|"
    r"injection|inhaler|spray|suppository)s?\b",
    re.IGNORECASE,
)
NON_MEDICATION_RE = re.compile(
    r"^\s*(none recorded|none|no known|n/?a|not recorded)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class MedicationFact:
    medication_name: str
    route: str | None
    source_page: int | None


def _clean_name(line: str) -> str:
    """The drug name alone: strength, dose form, route and separators removed.

    The two chart families write the same fact three ways — "nebivolol Oral",
    "alendronate — Oral", "meloxicam 15 mg Oral - tablet" — and all three name
    the same drug.
    """
    name = ROUTE_RE.sub(" ", line)
    name = STRENGTH_RE.sub(" ", name)
    name = DOSE_FORM_RE.sub(" ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" -—–,·").strip()


def parse_medications(sidebar_blocks: list[Block]) -> list[MedicationFact]:
    if not sidebar_blocks:
        return []
    sections = find_sections(sidebar_blocks)
    facts: list[MedicationFact] = []
    for block in reading_order(sections.get("current_medications", [])):
        line = block.text.strip()
        if not line or NON_MEDICATION_RE.match(line):
            continue
        route_match = ROUTE_RE.search(line)
        name = _clean_name(line)
        if name:
            facts.append(MedicationFact(
                medication_name=name.lower(),
                route=route_match.group(1) if route_match else None,
                source_page=block.page,
            ))
    return facts
