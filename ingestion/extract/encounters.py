"""Split a multi-visit document into encounters.

The provided chart holds two visits in one PDF, and the page counter restarts
at the boundary. Two independent signals start a new encounter:

  1. the page counter resetting to 1, and
  2. the date of service changing.

Using both means a chart that drops one signal still splits correctly. Getting
this wrong collapses two visits into one row and destroys the encounter grain,
which is the spine of the whole model.
"""

import re
from dataclasses import dataclass
from datetime import date

from ingestion.extract.layout import PageLayout, text_of

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
LONG_DATE_RE = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.IGNORECASE,
)
# "Visit Note - July 23, 2025" is the provided chart's own encounter header;
# the rendered corpus prints "Date of Service: 07/23/2025" instead.
SERVICE_DATE_RE = re.compile(
    r"(?:Date of Service|DOS|Encounter Date|Visit Date|Visit Note)"
    r"\s*[:\-–—]?\s*"
    r"((?:\d{1,2}/\d{1,2}/\d{4})|(?:[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}))",
    re.IGNORECASE,
)
DOB_LABEL_RE = re.compile(r"DOB\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)


@dataclass(frozen=True)
class EncounterPages:
    encounter_date: date | None
    page_start: int
    page_end: int
    pages: list[PageLayout]


def find_dates(text: str) -> list[date]:
    """Every readable date in the text, in the order it appears.

    Both spellings are accepted because both occur: the rendered corpus writes
    07/23/2025 and the provided export writes July 23, 2025.
    """
    found: list[tuple[int, date]] = []
    for match in NUMERIC_DATE_RE.finditer(text):
        month, day, year = (int(g) for g in match.groups())
        try:
            found.append((match.start(), date(year, month, day)))
        except ValueError:
            continue  # 13/45/2025 and friends
    for match in LONG_DATE_RE.finditer(text):
        month_name, day, year = match.groups()
        try:
            found.append((match.start(), date(int(year), MONTHS[month_name.lower()], int(day))))
        except ValueError:
            continue
    return [value for _, value in sorted(found, key=lambda pair: pair[0])]


def service_date_of(page: PageLayout, date_of_birth: date | None) -> date | None:
    """The visit date for a page: a labelled date of service if present,
    otherwise the first plausible date that is not the date of birth."""
    scope = f"{text_of(page.header)} {text_of(page.body)}"
    labelled = SERVICE_DATE_RE.search(scope)
    if labelled:
        found = find_dates(labelled.group(1))
        if found:
            return found[0]

    excluded = {date_of_birth} if date_of_birth else set()
    dob_in_text = DOB_LABEL_RE.search(scope)
    if dob_in_text:
        excluded.update(find_dates(dob_in_text.group(1)))
    for candidate in find_dates(scope):
        if candidate not in excluded:
            return candidate
    return None


def split_encounters(
    pages: list[PageLayout], date_of_birth: date | None = None
) -> list[EncounterPages]:
    if not pages:
        return []

    groups: list[list[PageLayout]] = [[pages[0]]]
    dates: list[date | None] = [service_date_of(pages[0], date_of_birth)]

    for page in pages[1:]:
        current_date = service_date_of(page, date_of_birth)
        counter_reset = page.page_label == 1
        date_changed = (
            current_date is not None
            and dates[-1] is not None
            and current_date != dates[-1]
        )
        if counter_reset or date_changed:
            groups.append([page])
            dates.append(current_date)
        else:
            groups[-1].append(page)
            if dates[-1] is None:
                dates[-1] = current_date

    return [
        EncounterPages(
            encounter_date=group_date,
            page_start=group[0].page,
            page_end=group[-1].page,
            pages=group,
        )
        for group, group_date in zip(groups, dates)
    ]
