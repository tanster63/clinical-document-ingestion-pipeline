from datetime import date

from ingestion.keys import (
    diagnosis_key, document_key, encounter_key, history_key, imaging_key,
    issue_key, patient_key, prescription_key, procedure_key, sha256_key,
)


def test_sha256_key_is_deterministic_and_hex():
    first = sha256_key("a", 1, date(2025, 7, 23))
    assert first == sha256_key("a", 1, date(2025, 7, 23))
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")


def test_field_boundaries_cannot_be_smeared():
    """'ab' + 'c' must not collide with 'a' + 'bc' — that is how two different
    prescriptions quietly become one row."""
    assert sha256_key("ab", "c") != sha256_key("a", "bc")


def test_patient_key_is_the_mrn_and_tolerates_formatting():
    assert patient_key("4820917") == "4820917"
    assert patient_key(" 4820917 ") == patient_key("4820917")
    assert patient_key("4820917") != patient_key("4820918")


def test_encounter_key_is_stable_across_a_re_export():
    """The same visit re-exported in a different document yields the same key,
    which is what makes MERGE idempotent at encounter grain."""
    first = encounter_key(patient_key("4820917"), date(2025, 7, 23), "Marla Whitcomb")
    second = encounter_key(patient_key("4820917"), date(2025, 7, 23), "marla  whitcomb")
    assert first == second
    assert first != encounter_key(patient_key("4820917"), date(2025, 8, 13), "Marla Whitcomb")


def test_encounter_key_tolerates_a_missing_provider():
    assert encounter_key("p1", date(2025, 7, 23), None) == encounter_key("p1", date(2025, 7, 23), "")


def test_document_key_is_the_content_hash():
    """Re-uploading identical bytes under a new object name is the same
    document, not a second one."""
    assert document_key(b"abc") == document_key(b"abc")
    assert document_key(b"abc") != document_key(b"abd")
    assert len(document_key(b"abc")) == 64


def test_child_keys_separate_on_their_business_identity():
    assert diagnosis_key("e1", "M25.511", "Pain in right shoulder") != \
        diagnosis_key("e1", "M25.512", "Pain in left shoulder")
    assert diagnosis_key("e1", None, "Acromioclavicular arthritis") != \
        diagnosis_key("e2", None, "Acromioclavicular arthritis")
    assert prescription_key("e1", "meloxicam", "15", "qd") != \
        prescription_key("e1", "meloxicam", "7.5", "qd")
    assert imaging_key("e1", "XR", "shoulder", date(2025, 7, 23)) != \
        imaging_key("e1", "MRI", "shoulder", date(2025, 7, 23))


def test_issue_keys_do_not_accumulate_across_runs_or_re_exports():
    """An issue row is keyed on the gap — patient, visit, type, field — not on
    the run or the file that revealed it. Keying on the document would make a
    corrected re-export report every one of its predecessor's gaps again, and
    `document_id` is the file's content hash, so a re-export is a new document."""
    gap = ("4820917", date(2025, 8, 13), "missing_section", "vitals")
    assert issue_key(*gap) == issue_key(*gap)
    assert issue_key(*gap) != issue_key("4820917", date(2025, 7, 23),
                                        "missing_section", "vitals")
    assert issue_key(*gap) != issue_key("4820917", date(2025, 8, 13),
                                        "missing_section", "imaging")


def test_history_is_keyed_at_patient_grain():
    """The rail reprints the same history at every visit; one fact, one row."""
    assert history_key("4820917", "medical", "Essential hypertension") == \
        history_key("4820917", "medical", "essential  hypertension")
    assert history_key("4820917", "medical", "Essential hypertension") != \
        history_key("4820918", "medical", "Essential hypertension")


def test_a_procedure_is_keyed_on_what_was_done_and_when():
    assert procedure_key("e1", "Left L5-S1 microdiscectomy", date(2025, 7, 2)) != \
        procedure_key("e1", "Left L5-S1 microdiscectomy", date(2025, 7, 9))
