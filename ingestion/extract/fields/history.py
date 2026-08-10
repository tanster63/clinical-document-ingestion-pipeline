"""Longitudinal history from the left rail.

The brief points at this directly: "The left sidebar carries longitudinal
context that is true of the patient rather than of the visit." So it is modelled
on the patient, unlike the medication list printed beside it, which changes
between visits and is captured per encounter.

"None" is not a history item. A chart that records nothing under a heading is
saying the clinician looked and found nothing, which is an absence, not a fact —
so it produces no row, and the absence shows up as a patient with no history of
that type rather than as a row that says "None".
"""

import re
from dataclasses import dataclass

from ingestion.extract.layout import Block, reading_order
from ingestion.extract.sections import find_sections

# Sidebar section key -> the history_type stored in the warehouse.
HISTORY_SECTIONS: dict[str, str] = {
    "medical_history": "medical",
    "musculoskeletal_history": "musculoskeletal",
    "family_history": "family",
    "musculoskeletal_surgery": "musculoskeletal_surgery",
    "surgical_history": "surgical",
    "social_history": "social",
    "allergies": "allergy",
}
NOT_RECORDED_RE = re.compile(
    r"^\s*(none(\s+recorded)?|no known[\w\s]*|n/?a|not recorded|negative|denies)\s*\.?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HistoryFact:
    history_type: str
    item_text: str
    source_page: int | None


def parse_history(sidebar_blocks: list[Block]) -> list[HistoryFact]:
    if not sidebar_blocks:
        return []
    sections = find_sections(sidebar_blocks)
    facts: list[HistoryFact] = []
    seen: set[tuple[str, str]] = set()
    for key, history_type in HISTORY_SECTIONS.items():
        for block in reading_order(sections.get(key, [])):
            item = " ".join(block.text.split()).strip(" .,;")
            if not item or NOT_RECORDED_RE.match(item):
                continue
            marker = (history_type, item.lower())
            if marker in seen:
                continue
            seen.add(marker)
            facts.append(HistoryFact(history_type=history_type, item_text=item,
                                     source_page=block.page))
    return facts
