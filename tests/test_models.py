from datetime import date, datetime

import pytest
from pydantic import ValidationError

from ingestion.models import (
    Diagnosis, Document, Encounter, ExamFinding, ImagingStudy, MedicationSnapshot,
    Patient, Prescription, Vitals,
)


def test_patient_requires_an_mrn():
    with pytest.raises(ValidationError):
        Patient(patient_id="p1", mrn="", family_name="BARLOW")


def test_patient_derives_nothing_it_was_not_given():
    p = Patient(patient_id="4820917", mrn="4820917", family_name="BARLOW",
                given_name="TREMAINE", preferred_name="Trey Barlow")
    assert p.preferred_name == "Trey Barlow"
    assert p.date_of_birth is None
    assert p.phone_home is None


def test_patient_sex_is_a_closed_vocabulary():
    with pytest.raises(ValidationError):
        Patient(patient_id="p1", mrn="1", sex="Male")


def test_encounter_rejects_an_out_of_vocabulary_visit_type():
    with pytest.raises(ValidationError):
        Encounter(encounter_id="e1", patient_id="p1", encounter_date=date(2025, 7, 23),
                  visit_type="telehealth")


def test_encounter_accepts_all_null_llm_columns():
    e = Encounter(encounter_id="e1", patient_id="p1", encounter_date=date(2025, 7, 23))
    assert e.body_region is None
    assert e.hpi_summary is None
    assert e.llm_confidence is None
    assert e.llm_model is None


def test_a_typo_in_a_column_name_is_refused_rather_than_silently_dropped():
    with pytest.raises(ValidationError):
        Encounter(encounter_id="e1", patient_id="p1", encounter_date=date(2025, 7, 23),
                  chief_complaint="typo for chief_complaint_raw")


def test_vitals_rejects_a_physiologically_impossible_value():
    with pytest.raises(ValidationError):
        Vitals(encounter_id="e1", patient_id="p1", bp_systolic=900)


def test_vitals_accepts_the_provided_charts_sparse_row():
    v = Vitals(encounter_id="e1", patient_id="p1", height_in=67.0, weight_lbs=273.2,
               bmi=42.8, bsa=2.3, is_patient_reported=True)
    assert v.pulse is None
    assert v.is_patient_reported is True


def test_diagnosis_rejects_a_malformed_icd10_code():
    with pytest.raises(ValidationError):
        Diagnosis(diagnosis_id="x1", encounter_id="e1", patient_id="p1",
                  diagnosis_text="Knee pain", icd10_code="XX9")


def test_diagnosis_accepts_a_code_free_diagnosis():
    d = Diagnosis(diagnosis_id="x1", encounter_id="e1", patient_id="p1",
                  diagnosis_text="Acromioclavicular arthritis")
    assert d.icd10_code is None


def test_prescription_refills_cannot_be_negative():
    with pytest.raises(ValidationError):
        Prescription(prescription_id="r1", encounter_id="e1", patient_id="p1",
                     drug_name="meloxicam", sig_text="Take 1 po qd", refills=-1)


def test_prescription_action_is_a_closed_vocabulary():
    with pytest.raises(ValidationError):
        Prescription(prescription_id="r1", encounter_id="e1", patient_id="p1",
                     drug_name="meloxicam", action="renew")


def test_medication_snapshot_carries_no_patient_level_validity():
    """Its grain is (encounter x medication): it is only true as of that visit."""
    m = MedicationSnapshot(encounter_id="e1", patient_id="p1", medication_name="nebivolol")
    assert set(m.to_row()) == {
        "encounter_id", "patient_id", "medication_name", "strength",
        "strength_unit", "dose_form", "route", "source_document_id", "source_page",
    }


def test_imaging_and_exam_findings_use_the_shared_laterality_vocabulary():
    with pytest.raises(ValidationError):
        ImagingStudy(imaging_id="i1", encounter_id="e1", patient_id="p1",
                     modality="XR", laterality="dorsal")
    with pytest.raises(ValidationError):
        ExamFinding(finding_id="f1", encounter_id="e1", patient_id="p1",
                    finding_type="not_a_type")


def test_model_dump_is_bigquery_ready():
    """Dates and datetimes must serialize to strings the BQ loader accepts."""
    e = Encounter(encounter_id="e1", patient_id="p1", encounter_date=date(2025, 7, 23),
                  signed_at=datetime(2025, 7, 23, 14, 37))
    row = e.to_row()
    assert row["encounter_date"] == "2025-07-23"
    assert row["signed_at"].startswith("2025-07-23T14:37:00")


def test_document_row_matches_the_declared_ddl_columns():
    d = Document(document_id="a" * 64, gcs_uri="gs://b/c.pdf", file_name="c.pdf",
                 file_bytes=10, page_count=5, ingest_run_id="r1")
    assert set(d.to_row()) == {
        "document_id", "gcs_uri", "file_name", "file_bytes", "page_count",
        "mrn_from_filename", "pms_id_from_filename", "ingested_at",
        "ingest_run_id", "pipeline_version", "parse_status",
    }
