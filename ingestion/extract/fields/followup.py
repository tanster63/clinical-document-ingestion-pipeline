"""Follow-up interval, normalized to days (§6.1).

The raw phrasing is kept alongside the number. "Return as needed" carries real
clinical meaning that no integer can hold, and losing it to a NULL would be a
worse answer than storing both.
"""

import re

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
FOLLOW_UP_RE = re.compile(
    r"(?:follow[\s-]?up|return|rtc|recheck|see (?:him|her|them|the patient))"
    r"[^.\n]{0,40}?"
    r"\b(?P<n>\d+|" + "|".join(WORD_NUMBERS) + r")\s*"
    r"(?P<unit>day|week|month|year)s?\b",
    re.IGNORECASE,
)


PHRASE_RE = re.compile(
    r"(?:follow[\s-]?up|return|rtc|recheck)[^.\n]{0,60}", re.IGNORECASE
)
# A whole plan section is not a follow-up instruction. When no recognisable
# phrase is present, only a short input is kept verbatim.
MAX_RAW_CHARS = 120


def parse_follow_up(text: str) -> tuple[int | None, str | None]:
    """Return (interval_in_days, raw_phrase).

    The raw phrasing is preserved even when no interval can be read, because
    "return as needed" is a real instruction that no integer can represent.
    """
    raw = text.strip()
    if not raw:
        return None, None
    match = FOLLOW_UP_RE.search(raw)
    if match:
        token = match.group("n").lower()
        count = int(token) if token.isdigit() else WORD_NUMBERS[token]
        return count * UNIT_DAYS[match.group("unit").lower()], match.group(0).strip()
    phrase = PHRASE_RE.search(raw)
    if phrase:
        return None, phrase.group(0).strip()
    return None, raw if len(raw) <= MAX_RAW_CHARS else None
