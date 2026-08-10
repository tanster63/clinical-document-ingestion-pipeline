"""One shared shape for every gap the pipeline detects, so a parser can record a
problem without knowing anything about BigQuery."""

from dataclasses import dataclass
from datetime import date

SEVERITY_WARN = "warn"
SEVERITY_ERROR = "error"


@dataclass(frozen=True)
class IssueDraft:
    severity: str
    issue_type: str
    detail: str
    field_name: str | None = None
    encounter_date: date | None = None


def warn(issue_type: str, detail: str, field_name: str | None = None,
         encounter_date: date | None = None) -> IssueDraft:
    return IssueDraft(SEVERITY_WARN, issue_type, detail, field_name, encounter_date)


def error(issue_type: str, detail: str, field_name: str | None = None,
          encounter_date: date | None = None) -> IssueDraft:
    return IssueDraft(SEVERITY_ERROR, issue_type, detail, field_name, encounter_date)
