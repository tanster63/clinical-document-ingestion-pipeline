"""Bucket blocks under normalized section headings.

Two heading shapes occur in real exports and both have to work:

* a heading on its own line (``VITALS``, ``Impression/Plan:``), which is what a
  templated renderer produces, and
* a heading that shares its line with the first sentence of its own content
  (``HPI: This is a 33 year old male who:``), which is what the provided EMR
  export produces.

The second shape is why headings are not simply matched whole-line. Only a
short, explicit list of labels is allowed to split a line, because a label such
as ``Impression:`` also occurs *inside* an imaging paragraph, where treating it
as a section break would tear the study's own impression out of it.
"""

import re

from ingestion.extract.layout import Block, reading_order, text_of

HEADING_ALIASES: dict[str, str] = {
    # --- body -----------------------------------------------------------------
    "chief complaint": "chief_complaint",
    "chief complaints": "chief_complaint",
    "cc": "chief_complaint",
    "reason for visit": "chief_complaint",
    "history of present illness": "hpi",
    "hpi": "hpi",
    "subjective": "hpi",
    "vitals": "vitals",
    "vital signs": "vitals",
    "physical exam": "exam",
    "exam": "exam",
    "objective": "exam",
    "musculoskeletal exam": "exam",
    "imaging": "imaging",
    "radiology": "imaging",
    "imaging results": "imaging",
    "tests": "imaging",
    "operative note": "operative_note",
    "procedure note": "operative_note",
    "assessment": "assessment",
    "impression": "assessment",
    "assessment and plan": "assessment",
    "impression/plan": "assessment",
    "diagnoses": "assessment",
    "prescriptions": "prescriptions",
    "medications prescribed": "prescriptions",
    "new prescriptions": "prescriptions",
    "plan": "plan",
    "treatment plan": "plan",
    "follow up": "plan",
    "note": "note",
    "notes": "note",
    "staff": "staff",
    "signature": "signature",
    "electronically signed": "signature",
    # --- sidebar --------------------------------------------------------------
    "current medications": "current_medications",
    "medications": "current_medications",
    "allergies": "allergies",
    "location": "location",
    "clinic": "location",
    "medical history": "medical_history",
    "surgical history": "surgical_history",
    "musculoskeletal surgery": "musculoskeletal_surgery",
    "social history": "social_history",
    "family history": "family_history",
    "musculoskeletal family history": "family_history",
    "musculoskeletal history": "musculoskeletal_history",
    "musculoskeletal family": "family_history",
    # The provided chart wraps "Musculoskeletal" and "History" onto separate
    # lines, so each half has to be recognised on its own.
    "musculoskeletal": "musculoskeletal_history",
    "history": "musculoskeletal_history",
}

# Labels permitted to split a line and keep the remainder as content. Kept
# deliberately narrow: `Impression:` is a section heading on its own line but a
# field label inside an imaging paragraph, so it is not in this set.
INLINE_HEADINGS = frozenset({
    "chief complaint", "chief complaints", "hpi", "history of present illness",
    "impression/plan", "assessment and plan", "plan", "note", "notes", "staff",
    "subjective", "objective",
})

SECTION_KEYS = sorted(set(HEADING_ALIASES.values()) | {"_preamble"})
PREAMBLE = "_preamble"
MAX_HEADING_WORDS = 4
INLINE_RE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z /]{0,40}?)\s*:\s*(?P<rest>.*)$")


def normalize_heading(text: str) -> str | None:
    """The canonical section key for a heading line, or None if the line is
    prose. Headings are short and match the alias table exactly."""
    cleaned = re.sub(r"[:\-\s]+$", "", text.strip()).strip()
    if not cleaned or len(cleaned.split()) > MAX_HEADING_WORDS:
        return None
    return HEADING_ALIASES.get(cleaned.lower())


def split_heading(text: str) -> tuple[str | None, str]:
    """Split a line into (section key, remaining content).

    Returns ``(None, text)`` when the line is not a heading at all, and
    ``(key, "")`` when the whole line is the heading.
    """
    key = normalize_heading(text)
    if key:
        return key, ""
    match = INLINE_RE.match(text.strip())
    if not match:
        return None, text
    label = " ".join(match.group("label").split()).lower()
    if label not in INLINE_HEADINGS:
        return None, text
    return HEADING_ALIASES[label], match.group("rest").strip()


def find_sections(blocks: list[Block]) -> dict[str, list[Block]]:
    """Group blocks by the section heading that precedes them.

    Content that shared a line with its heading is re-emitted as a block with
    the heading text removed and the original geometry preserved, so parsers
    that work in coordinates (vitals, identity) are unaffected.
    """
    sections: dict[str, list[Block]] = {}
    current = PREAMBLE
    ordered = reading_order(blocks)
    index = 0
    while index < len(ordered):
        block = ordered[index]

        # A narrow rail wraps a long heading across two lines, and where it
        # breaks depends on the column width: the provided chart splits
        # "Musculoskeletal / Family History", a wider rail splits
        # "Musculoskeletal Family / History". Either way the two halves name one
        # section, and reading them separately files the content under whichever
        # half came last.
        if index + 1 < len(ordered):
            joined = f"{block.text.strip()} {ordered[index + 1].text.strip()}"
            joined_key = normalize_heading(joined)
            if joined_key:
                current = joined_key
                sections.setdefault(current, [])
                index += 2
                continue

        key, remainder = split_heading(block.text)
        if key:
            current = key
            sections.setdefault(current, [])
            if remainder:
                sections[current].append(
                    Block(text=remainder, x0=block.x0, y0=block.y0,
                          x1=block.x1, y1=block.y1, page=block.page)
                )
            index += 1
            continue
        sections.setdefault(current, []).append(block)
        index += 1
    return sections


def section_text(sections: dict[str, list[Block]], key: str) -> str:
    return text_of(sections.get(key, []))


def section_blocks(sections: dict[str, list[Block]], *keys: str) -> list[Block]:
    """Blocks from several sections at once, back in reading order."""
    collected = [block for key in keys for block in sections.get(key, [])]
    return reading_order(collected)
