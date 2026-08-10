"""Imaging studies within an encounter.

The two chart families describe a study very differently. The rendered corpus
writes one self-contained line:

    XR pelvis and right hip (right) — performed 04/22/2025

The provided export spreads one study over several paragraphs, each of which
opens with the modality again:

    X-Ray Interpretation Shoulder
    X-Ray Data:
      Date:  07/23/2025
    X-rays of the right shoulder were ordered and obtained, ...

Segmenting on every modality mention would turn that single study into three.
Segments are therefore merged when they describe the same modality and body
part, which collapses the export's paragraphs back into one study while leaving
genuinely distinct studies — an XR and an MRI of the same region — separate.
"""

import re
from dataclasses import dataclass
from datetime import date

from ingestion.extract.layout import Block, lines_of

MODALITY_RE = re.compile(
    r"\b(?P<modality>X-?rays?|XR|MRI|CT|US|Ultrasound|DEXA|Fluoroscopy|Radiographs?)\b",
    re.IGNORECASE,
)
STUDY_START_RE = re.compile(r"^\s*(?:[-•*·]\s*)?(?:" + MODALITY_RE.pattern + r")", re.IGNORECASE)
MODALITY_NORMALIZED = {"xray": "XR", "xrays": "XR", "xr": "XR", "radiograph": "XR",
                       "radiographs": "XR", "ultrasound": "US"}
PERFORMED_RE = re.compile(
    r"(?:performed|date)\s*:?\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE
)
IMPRESSION_RE = re.compile(r"Impression\s*:?\s*(?P<text>.+)", re.IGNORECASE | re.DOTALL)
PAREN_SIDE_RE = re.compile(r"\((left|right|bilateral)\)", re.IGNORECASE)
SIDE_WORD_RE = re.compile(r"\b(left|right|bilateral)\b", re.IGNORECASE)
NOISE_WORDS_RE = re.compile(
    r"\b(?:interpretation|data|results?|study|studies|series|views?|films?|"
    r"weight[- ]bearing|obtained|ordered)\b",
    re.IGNORECASE,
)
BODY_PARTS = (
    "lumbar spine", "cervical spine", "thoracic spine", "sacroiliac joint",
    "shoulder", "elbow", "wrist", "hand", "finger", "thumb", "hip", "pelvis",
    "knee", "ankle", "foot", "toe", "spine", "clavicle", "scapula", "humerus",
    "forearm", "femur", "patella", "tibia", "fibula", "calcaneus",
)
MAX_BODY_PART_WORDS = 5


@dataclass(frozen=True)
class ImagingFact:
    modality: str
    body_part: str | None
    laterality: str | None
    performed_date: date | None
    interpretation_text: str | None
    impression: str | None
    source_page: int | None


def _normalize_modality(raw: str) -> str:
    key = raw.replace("-", "").lower()
    return MODALITY_NORMALIZED.get(key, raw.upper())


def _lead(after_modality: str) -> str:
    """The part of an opening line that names the study, before the report of it.

    Everything after the first delimiter is findings prose, and findings
    describe sides and levels that are not the study's own.
    """
    return re.split(r"[(:.—–\-]|\bperformed\b", after_modality,
                    maxsplit=1, flags=re.IGNORECASE)[0]


def _body_part(lead: str, segment: str) -> str | None:
    """The imaged region: whatever names it on the opening line, falling back to
    the first anatomical term anywhere in the segment."""
    cleaned = NOISE_WORDS_RE.sub("", lead)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -—–,").lower()
    if cleaned and len(cleaned.split()) <= MAX_BODY_PART_WORDS:
        return cleaned
    lowered = segment.lower()
    for part in BODY_PARTS:
        if re.search(rf"\b{re.escape(part)}\b", lowered):
            return part
    return None


def _laterality(head: str, lead: str, segment: str, body_part: str | None) -> str | None:
    """The side the *study* was of.

    Deliberately not "the first side word anywhere in the report": an MRI of the
    lumbar spine whose impression describes a left-sided disc herniation is not
    a left-sided study, and recording it as one would put a laterality on a
    region that has none.
    """
    paren = PAREN_SIDE_RE.search(head)
    if paren:
        return paren.group(1).lower()
    named = SIDE_WORD_RE.search(lead)
    if named:
        return named.group(1).lower()
    if body_part:
        beside = re.search(rf"\b(left|right|bilateral)\s+{re.escape(body_part)}\b",
                           segment, re.IGNORECASE)
        if beside:
            return beside.group(1).lower()
    return None


def _performed(segment: str, fallback: date | None) -> date | None:
    match = PERFORMED_RE.search(segment)
    if not match:
        return fallback
    month, day, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return fallback


def _merge(into: ImagingFact, extra: ImagingFact) -> ImagingFact:
    joined = " ".join(
        part for part in (into.interpretation_text, extra.interpretation_text) if part
    )
    return ImagingFact(
        modality=into.modality,
        body_part=into.body_part or extra.body_part,
        laterality=into.laterality or extra.laterality,
        performed_date=into.performed_date or extra.performed_date,
        interpretation_text=joined or None,
        impression=into.impression or extra.impression,
        source_page=into.source_page,
    )


def parse_imaging(blocks: list[Block], encounter_date: date | None) -> list[ImagingFact]:
    if not blocks:
        return []
    lines = lines_of(blocks)
    starts = [index for index, line in enumerate(lines) if STUDY_START_RE.match(line.text)]
    if not starts:
        return []

    facts: list[ImagingFact] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        head = lines[start].text
        segment = " ".join(line.text for line in lines[start:end])

        modality_match = MODALITY_RE.search(head)
        modality = _normalize_modality(modality_match.group("modality"))
        after_modality = head[modality_match.end():]

        impression_match = IMPRESSION_RE.search(segment)
        impression = impression_match.group("text").strip() if impression_match else None
        body = segment[: impression_match.start()] if impression_match else segment
        body = body[modality_match.end():] if position == 0 else body
        body = PERFORMED_RE.sub("", body).strip(" -—–,:")

        lead = _lead(after_modality)
        body_part = _body_part(lead, segment)

        candidate = ImagingFact(
            modality=modality,
            body_part=body_part,
            laterality=_laterality(head, lead, segment, body_part),
            performed_date=_performed(segment, encounter_date),
            interpretation_text=body or None,
            impression=impression,
            source_page=lines[start].page,
        )
        if facts and facts[-1].modality == candidate.modality and (
            candidate.body_part is None or facts[-1].body_part == candidate.body_part
        ):
            facts[-1] = _merge(facts[-1], candidate)
            continue
        facts.append(candidate)
    return facts
