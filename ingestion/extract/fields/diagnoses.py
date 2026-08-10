"""Diagnoses from the assessment section.

`body_region` and `laterality` here are deterministic (§4.2): resolved from the
ICD-10 code where the code encodes them, otherwise left NULL for the pipeline to
inherit from the encounter. The LLM never touches these columns.

The provided export prints a numbered *problem*, then the coded diagnosis
beneath it:

    1.  Shoulder Pain, Right
        Pain in right shoulder (M25.511)
        Associated diagnosis: Acromioclavicular Arthritis

Emitting all three would triple-count one diagnosis, so an enumerated line that
is elaborated by a coded line beneath it is treated as the problem title it is,
and the coded line is the diagnosis.
"""

import re
from dataclasses import dataclass, replace

from ingestion.extract.layout import Block, lines_of

ICD10_RE = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,4})?)\b")
PRIMARY_MARKER_RE = re.compile(r"\[\s*primary\s*\]", re.IGNORECASE)
BULLET_RE = re.compile(r"^\s*(?:[-•*·]|\d+[.)])\s*")
ENUMERATED_RE = re.compile(r"^\s*\d+[.)]\s*")
LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:associated\s+diagnos[ei]s|diagnos[ei]s|assessment|problem)\s*:\s*",
    re.IGNORECASE,
)

# Longest prefix wins. Only codes whose region is unambiguous are listed.
REGION_BY_PREFIX: dict[str, str] = {
    "M25.51": "shoulder", "M25.52": "elbow", "M25.53": "wrist", "M25.54": "hand",
    "M25.55": "hip", "M25.56": "knee", "M25.57": "ankle",
    "M75": "shoulder", "M19.01": "shoulder", "M17": "knee", "M16": "hip",
    "M22": "knee", "M23": "knee", "M77.0": "elbow", "M77.1": "elbow",
    "M65.4": "wrist", "M18": "hand", "M72.2": "foot", "M79.67": "foot",
    "M76": "ankle", "M21.6": "ankle", "M51": "lumbar spine",
    "M54.5": "lumbar spine", "M54.16": "lumbar spine", "M48.06": "lumbar spine",
    "M50": "cervical spine", "M54.12": "cervical spine", "M54.2": "cervical spine",
    "M48.02": "cervical spine",
}

# Families whose final character is a laterality digit (1=right, 2=left, 9=unspecified).
LATERALIZED_FAMILIES = (
    "M25.51", "M25.52", "M25.53", "M25.54", "M25.55", "M25.56", "M25.57",
    "M17.1", "M16.1", "M77.0", "M77.1", "M79.67", "M75.0", "M75.1", "M75.4",
    "M76.6", "M18.1", "M19.01",
)
LATERALITY_DIGIT = {"1": "right", "2": "left", "9": None}


def body_region_from_icd10(code: str) -> str | None:
    code = code.upper()
    for prefix in sorted(REGION_BY_PREFIX, key=len, reverse=True):
        if code.startswith(prefix):
            return REGION_BY_PREFIX[prefix]
    return None


def laterality_from_icd10(code: str) -> str | None:
    code = code.upper()
    for family in LATERALIZED_FAMILIES:
        if code.startswith(family) and len(code) == len(family) + 1:
            return LATERALITY_DIGIT.get(code[-1])
    return None


@dataclass(frozen=True)
class DiagnosisFact:
    icd10_code: str | None
    icd10_description: str | None
    diagnosis_text: str
    is_primary: bool
    body_region: str | None
    laterality: str | None
    source: str
    source_page: int | None


def _superseded(index: int, texts: list[str]) -> bool:
    """True when an enumerated problem title is followed by its own coded
    diagnosis before the next enumerated problem starts."""
    if not ENUMERATED_RE.match(texts[index]):
        return False
    for follower in texts[index + 1:]:
        if ENUMERATED_RE.match(follower):
            return False
        if ICD10_RE.search(follower):
            return True
    return False


def parse_diagnoses(blocks: list[Block], source: str = "impression") -> list[DiagnosisFact]:
    lines = lines_of(blocks)
    texts = [line.text for line in lines]
    any_primary_flag = any(PRIMARY_MARKER_RE.search(text) for text in texts)

    facts: list[DiagnosisFact] = []
    for index, line in enumerate(lines):
        if _superseded(index, texts):
            continue
        cleaned = LABEL_PREFIX_RE.sub("", BULLET_RE.sub("", line.text)).strip()
        if not cleaned:
            continue
        is_primary = bool(PRIMARY_MARKER_RE.search(cleaned))
        cleaned = PRIMARY_MARKER_RE.sub("", cleaned).strip()
        if is_primary and not cleaned:
            # A long diagnosis wraps and leaves "[Primary]" alone on the next
            # line. The marker still belongs to the diagnosis above it.
            if facts:
                facts[-1] = replace(facts[-1], is_primary=True)
            continue

        code_match = ICD10_RE.search(cleaned)
        code = code_match.group(1) if code_match else None
        text = cleaned
        if code:
            text = re.sub(r"\(?\s*" + re.escape(code) + r"\s*\)?", "", text)
        text = text.strip(" ,;-—–").strip()
        if not text and not code:
            continue

        if not any_primary_flag and not facts:
            is_primary = True

        facts.append(DiagnosisFact(
            icd10_code=code,
            # What the chart prints beside a code *is* the code's description.
            icd10_description=text if code else None,
            diagnosis_text=text,
            is_primary=is_primary,
            body_region=body_region_from_icd10(code) if code else None,
            laterality=laterality_from_icd10(code) if code else None,
            source=source,
            source_page=line.page,
        ))
    return facts
