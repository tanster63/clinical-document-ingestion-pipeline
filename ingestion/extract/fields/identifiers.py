"""Patient identity from the header band, cross-checked against the filename.

Identity is read in coordinates, not in reading order, and this is not a
stylistic choice. In the provided export the header prints its labels on one
row and their values on the row beneath, right-aligned per cell:

    PMS ID:   Sex:   DOB:        Phone:            MRN:
    4820917   Male   09/15/1991  (615) 555-0173    4820917

Flattened to a string that reads ``... MRN: 4820917 Male ...`` — so a regex for
``MRN:\\s*(\\d+)`` picks up the *PMS ID* and is wrong. It happens to be right on
this one chart because both identifiers carry the same value, which is exactly
the coincidence that hides the bug. Pairing each label with the value block
beneath it that overlaps it horizontally reads the right cell every time.

The filename also encodes MRN and PMS ID. A disagreement between the two is a
real signal about the source system, so it is recorded rather than silently
resolved (§6.2). The header wins, because it is what a human reads on the page.
"""

import re
from dataclasses import dataclass
from datetime import date

from ingestion.extract.layout import Block, text_of
from ingestion.issues import IssueDraft, warn

FILENAME_RE = re.compile(r"MRN(?P<mrn>\d+)_PMS(?P<pms>\d+)", re.IGNORECASE)
NAME_RE = re.compile(
    r"\b(?P<family>[A-Z][A-Z'\-]+(?:\s[A-Z][A-Z'\-]+)?),\s*"
    # A middle initial is one letter standing alone. Without the lookahead the
    # M of a following "MRN:" is read as one, and every patient acquires a
    # middle initial they do not have.
    r"(?P<given>[A-Z][A-Za-z'\-]+(?:\s[A-Z]\.?(?![A-Za-z]))?)"
    r"(?:\s*\((?P<preferred>[^)]{2,40})\))?"
)
SEX_NORMALIZED = {"m": "M", "male": "M", "f": "F", "female": "F",
                  "o": "O", "other": "O", "u": "U", "unknown": "U"}

# label pattern -> (field, value pattern). The label pattern must match the
# label cell on its own; the value pattern must match the value, whether it
# shares the cell (rendered corpus) or sits in the cell below (provided export).
FIELD_PATTERNS: dict[str, tuple[str, str]] = {
    "mrn": (r"\bMRN\b", r"(\d{4,12})"),
    "pms_id": (r"\bPMS(?:\s*ID)?\b", r"(\d{4,12})"),
    "date_of_birth": (r"\b(?:DOB|Date of Birth|Birth Date)\b",
                      r"(\d{1,2}/\d{1,2}/\d{4})"),
    "sex": (r"\b(?:Sex|Gender)\b", r"\b(Male|Female|M|F|Other|Unknown)\b"),
    "phone_home": (r"\b(?:Phone|Home(?:\s*Phone)?|Tel(?:ephone)?)\b",
                   r"(\(?\d{3}\)?[\s.\-]\s*\d{3}[\s.\-]\d{4})"),
}


@dataclass(frozen=True)
class PatientIdentity:
    mrn: str | None
    pms_id: str | None
    legal_name: str | None
    family_name: str | None
    given_name: str | None
    preferred_name: str | None
    date_of_birth: date | None
    sex: str | None
    phone_home: str | None


def parse_filename_ids(file_name: str) -> tuple[str | None, str | None]:
    match = FILENAME_RE.search(file_name)
    return (match.group("mrn"), match.group("pms")) if match else (None, None)


def _overlap(a: Block, b: Block) -> float:
    return min(a.x1, b.x1) - max(a.x0, b.x0)


def _value_for(label: Block, blocks: list[Block], inline_re: re.Pattern[str],
               value_re: re.Pattern[str]) -> str | None:
    """The value belonging to a label cell: on the same line if it is there,
    otherwise in the nearest line below that overlaps the label horizontally.

    The same-line search is anchored to the label. Searching the whole line for
    the first thing shaped like a value reads "MRN" off a line that begins
    "DOB: 09/15/1991" and returns 1991.
    """
    inline = inline_re.search(label.text)
    if inline:
        return inline.group(1)

    best: tuple[float, str] | None = None
    for candidate in blocks:
        if candidate is label or candidate.y0 <= label.y0:
            continue
        overlap = _overlap(label, candidate)
        if overlap <= 0:
            continue
        match = value_re.search(candidate.text)
        if not match:
            continue
        distance = candidate.y0 - label.y0
        if best is None or distance < best[0]:
            best = (distance, match.group(1))
    return best[1] if best else None


def _read_fields(header_blocks: list[Block]) -> dict[str, str]:
    """Every identity field the header band yields, by label geometry."""
    found: dict[str, str] = {}
    for field, (label_pattern, value_pattern) in FIELD_PATTERNS.items():
        label_re = re.compile(label_pattern, re.IGNORECASE)
        value_re = re.compile(value_pattern, re.IGNORECASE)
        inline_re = re.compile(label_pattern + r"\s*[:#]?\s*" + value_pattern, re.IGNORECASE)
        for block in header_blocks:
            if not label_re.search(block.text):
                continue
            value = _value_for(block, header_blocks, inline_re, value_re)
            if value:
                found[field] = value.strip()
                break
    return found


def _as_date(raw: str) -> date | None:
    month, day, year = (int(part) for part in raw.split("/"))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_identity(
    header_blocks: list[Block], file_name: str
) -> tuple[PatientIdentity, list[IssueDraft]]:
    issues: list[IssueDraft] = []
    fields = _read_fields(header_blocks)
    text = text_of(header_blocks)
    file_mrn, file_pms = parse_filename_ids(file_name)

    mrn = fields.get("mrn")
    if mrn and file_mrn and mrn != file_mrn:
        issues.append(warn(
            "identifier_mismatch",
            f"header MRN {mrn} disagrees with filename MRN {file_mrn}; using the header",
            field_name="mrn",
        ))
    if not mrn:
        mrn = file_mrn
        issues.append(warn(
            "unparsed_field",
            "no MRN found in the header band; fell back to the filename",
            field_name="mrn",
        ))

    pms_id = fields.get("pms_id")
    if pms_id and file_pms and pms_id != file_pms:
        issues.append(warn(
            "identifier_mismatch",
            f"header PMS ID {pms_id} disagrees with filename PMS ID {file_pms}",
            field_name="pms_id",
        ))
    pms_id = pms_id or file_pms

    date_of_birth = None
    if "date_of_birth" in fields:
        date_of_birth = _as_date(fields["date_of_birth"])
        if date_of_birth is None:
            issues.append(warn("unparsed_field",
                               f"unreadable date of birth {fields['date_of_birth']!r}",
                               field_name="date_of_birth"))
    else:
        issues.append(warn("unparsed_field", "no date of birth in the header band",
                           field_name="date_of_birth"))

    name = NAME_RE.search(text)
    preferred = name.group("preferred") if name else None

    identity = PatientIdentity(
        mrn=mrn,
        pms_id=pms_id,
        legal_name=name.group(0).strip() if name else None,
        family_name=name.group("family") if name else None,
        given_name=name.group("given") if name else None,
        preferred_name=preferred.strip() if preferred else None,
        date_of_birth=date_of_birth,
        sex=SEX_NORMALIZED.get(fields["sex"].lower()) if "sex" in fields else None,
        phone_home=fields.get("phone_home"),
    )
    return identity, issues
