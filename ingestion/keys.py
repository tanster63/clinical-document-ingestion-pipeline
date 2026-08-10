"""Deterministic keys.

Keys are derived from natural business identity, never from the file that
happened to carry the record. That is what makes MERGE idempotent when
the same encounter arrives inside two differently-sliced exports: a re-export
covering July and August produces the same two encounter keys as the original,
so the merge updates two rows instead of inserting two more.

`document_id` is the exception and is deliberately the sha256 of the file's own
bytes, so re-uploading an identical chart under a new object name records the
same document rather than a phantom second one.
"""

import hashlib
from datetime import date

SEPARATOR = "\x1f"  # ASCII unit separator: cannot occur in any extracted field


def sha256_key(*parts: object) -> str:
    """A hex digest over the parts, separated so field boundaries cannot smear.

    Without the separator, ("ab", "c") and ("a", "bc") would hash identically —
    which is how two different prescriptions quietly become one row.
    """
    joined = SEPARATOR.join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _normalize(value: str | None) -> str:
    return " ".join((value or "").split()).lower()


def patient_key(mrn: str) -> str:
    """The MRN itself. A single practice issues one MRN per patient, so hashing
    it would only make the warehouse harder to read. A multi-practice
    deployment would key on (practice_id, mrn) instead."""
    return " ".join((mrn or "").split())


def encounter_key(patient_id: str, encounter_date: date, provider_name: str | None) -> str:
    return sha256_key("encounter", patient_id, encounter_date.isoformat(),
                      _normalize(provider_name))


def document_key(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def diagnosis_key(encounter_id: str, icd10_code: str | None, diagnosis_text: str) -> str:
    return sha256_key("diagnosis", encounter_id, _normalize(icd10_code),
                      _normalize(diagnosis_text))


def prescription_key(encounter_id: str, drug_name: str, strength: str | None,
                     sig_text: str | None) -> str:
    return sha256_key("prescription", encounter_id, _normalize(drug_name),
                      _normalize(strength), _normalize(sig_text))


def imaging_key(encounter_id: str, modality: str, body_part: str | None,
                performed_date: date | None) -> str:
    return sha256_key("imaging", encounter_id, _normalize(modality),
                      _normalize(body_part),
                      performed_date.isoformat() if performed_date else "")


def procedure_key(encounter_id: str, procedure_name: str, performed_date: date | None) -> str:
    return sha256_key("procedure", encounter_id, _normalize(procedure_name),
                      performed_date.isoformat() if performed_date else "")


def exam_finding_key(encounter_id: str, body_part: str | None, laterality: str | None,
                     finding_type: str, measure_name: str | None, ordinal: int) -> str:
    """Keyed on what the finding *is*, falling back to position only for
    narrative rows that carry no measure name to identify them by."""
    return sha256_key("exam", encounter_id, _normalize(body_part), _normalize(laterality),
                      finding_type, _normalize(measure_name),
                      "" if measure_name else ordinal)


def history_key(patient_id: str, history_type: str, item_text: str) -> str:
    return sha256_key("history", patient_id, history_type, _normalize(item_text))


def issue_key(scope: str, encounter_date: date | None, issue_type: str,
              field_name: str | None) -> str:
    """Keyed on the gap, not on the file that revealed it.

    Stable across re-runs, and — because `document_id` is the file's content
    hash — also across a genuine re-export. Keying on the document would make a
    corrected export report every one of its predecessor's gaps a second time.
    """
    return sha256_key("issue", scope,
                      encounter_date.isoformat() if encounter_date else "",
                      issue_type, _normalize(field_name))
