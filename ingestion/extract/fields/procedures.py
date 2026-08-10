"""Surgical procedures recorded at an encounter.

A procedure is neither a diagnosis nor a prescription: it is a thing that was
done, on a date that is often not the date of the visit that reports it. The
corpus's post-operative visit is exactly that case — the microdiscectomy happens
between two clinic appointments — and folding it into the encounter would put
the wrong date on the only surgical fact in the warehouse.

Only what the note prints is captured. The procedure's name, date and surgeon
are read from the labelled preamble; laterality and body part are resolved from
the procedure name against the same vocabularies the rest of the pipeline uses.
"""

import re
from dataclasses import dataclass
from datetime import date

from ingestion.extract.layout import Block, text_of

NAME_RE = re.compile(r"\bProcedure\s*:\s*(?P<name>[^\n]+?)(?=\s{2,}|\s*(?:Date|Surgeon)\s*:|$)",
                     re.IGNORECASE)
DATE_RE = re.compile(r"\bDate\s*:?\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE)
# A surgeon is a provider, so match the provider shape rather than "the rest of
# the line": PDF extraction collapses the line break after the name, and a
# greedy match swallows the entire operative narrative into the name field.
SURGEON_RE = re.compile(
    r"\bSurgeon\s*:\s*(?P<who>[A-Z][a-z]+(?:\s+[A-Z][a-z'\u2019\-]+)+\s*,\s*"
    r"(?:MD|DO|NP|PA-C|PA|DPM|APRN))",
)
SIDE_RE = re.compile(r"\b(left|right|bilateral)\b", re.IGNORECASE)
# A spinal level names the region even when the procedure's name does not:
# "L5-S1 microdiscectomy" is lumbar surgery and says so, in the only way an
# operative note ever does.
SPINAL_LEVEL_RE = re.compile(r"\b([CTLS])\d{1,2}\s*-\s*[CTLS]?\d{1,2}\b", re.IGNORECASE)
SPINE_BY_LEVEL = {"c": "cervical spine", "t": "thoracic spine",
                  "l": "lumbar spine", "s": "lumbar spine"}
BODY_PARTS = (
    "lumbar spine", "cervical spine", "thoracic spine", "shoulder", "elbow",
    "wrist", "hand", "hip", "knee", "ankle", "foot", "spine", "clavicle",
    "patella", "meniscus", "rotator cuff", "carpal tunnel",
)


@dataclass(frozen=True)
class ProcedureFact:
    procedure_name: str
    body_part: str | None
    laterality: str | None
    performed_date: date | None
    surgeon_name: str | None
    note_text: str | None
    source_page: int | None


def _body_part(name: str, note: str) -> str | None:
    for scope in (name, note):
        lowered = scope.lower()
        for part in BODY_PARTS:
            if part in lowered:
                return part
        level = SPINAL_LEVEL_RE.search(scope)
        if level:
            return SPINE_BY_LEVEL.get(level.group(1).lower())
    return None


def parse_procedures(blocks: list[Block], encounter_date: date | None) -> list[ProcedureFact]:
    if not blocks:
        return []
    text = text_of(blocks)
    name_match = NAME_RE.search(text)
    if not name_match:
        return []

    name = " ".join(name_match.group("name").split()).strip(" .")
    performed = encounter_date
    date_match = DATE_RE.search(text)
    if date_match:
        month, day, year = (int(g) for g in date_match.groups())
        try:
            performed = date(year, month, day)
        except ValueError:
            performed = encounter_date

    surgeon = SURGEON_RE.search(text)
    side = SIDE_RE.search(name)
    narrative = DATE_RE.sub("", SURGEON_RE.sub("", text[name_match.end():]))
    narrative = " ".join(narrative.split()).strip(" .:-")

    return [ProcedureFact(
        procedure_name=name,
        body_part=_body_part(name, narrative),
        laterality=side.group(1).lower() if side else None,
        performed_date=performed,
        surgeon_name=" ".join(surgeon.group("who").split()) if surgeon else None,
        note_text=narrative or None,
        source_page=blocks[0].page,
    )]
