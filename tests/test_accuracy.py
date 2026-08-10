from datetime import date

from corpus.spec_model import ChartSpec
from eval.accuracy import FieldResult, compare_document, overall, render_report
from ingestion.models import (
    Diagnosis, Document, Encounter, ExtractedDocument, ImagingStudy,
    MedicationSnapshot, Patient, Prescription, Vitals,
)


def truth() -> ChartSpec:
    return ChartSpec.model_validate({
        "chart_id": "chart_test",
        "file_name": "EMA_20250402T090000_0000_MRN6027418_PMS6027418_PID1_PatientChart_1.pdf",
        "location_name": "Cumberland Brentwood",
        "location_address": "1 Test Way, Brentwood TN",
        "patient": {"mrn": "6027418", "pms_id": "6027418", "family_name": "OKONKWO",
                    "given_name": "ADAEZE", "preferred_name": "Ada Okonkwo",
                    "date_of_birth": "1978-03-04", "sex": "F",
                    "phone_home": "(615) 555-0144"},
        "encounters": [{
            "encounter_date": "2025-04-02", "provider_name": "Marla Whitcomb",
            "provider_role": "NP", "chief_complaint": "Low back pain",
            "hpi_text": "Six weeks of low back pain radiating into the left leg.",
            "follow_up_raw": "Return in 3 weeks", "follow_up_days": 21,
            "body_region": "lumbar spine", "laterality": "left", "visit_type": "new",
            "vitals": {"bp_systolic": 132, "bp_diastolic": 84, "pulse": 76},
            "current_medications": [{"medication_name": "omeprazole", "route": "Oral"}],
            "diagnoses": [{"icd10_code": "M51.16", "diagnosis_text":
                           "Intervertebral disc disorders with radiculopathy, lumbar region",
                           "is_primary": True}],
            "prescriptions": [{"drug_name": "meloxicam", "strength": "15",
                               "strength_unit": "mg", "sig_text": "Take 1 po qd",
                               "quantity": 30, "refills": 1}],
            "imaging": [{"modality": "XR", "body_part": "lumbar spine",
                         "performed_date": "2025-04-02"}],
        }],
    })


def extracted(**overrides) -> ExtractedDocument:
    base = dict(
        patient=Patient(patient_id="6027418", mrn="6027418", family_name="OKONKWO",
                        given_name="ADAEZE", preferred_name="Ada Okonkwo",
                        date_of_birth=date(1978, 3, 4), sex="F",
                        phone_home="(615) 555-0144", pms_id="6027418"),
        encounters=[Encounter(encounter_id="e1", patient_id="6027418",
                              encounter_date=date(2025, 4, 2),
                              provider_name="Marla Whitcomb", provider_role="NP",
                              location_name="Cumberland Brentwood",
                              follow_up_interval_days=21, body_region="lumbar spine",
                              laterality="left", visit_type="new")],
        vitals=[Vitals(encounter_id="e1", patient_id="6027418", bp_systolic=132,
                       bp_diastolic=84, pulse=76)],
        diagnoses=[Diagnosis(diagnosis_id="x1", encounter_id="e1", patient_id="6027418",
                             icd10_code="M51.16",
                             diagnosis_text="Intervertebral disc disorders with "
                                            "radiculopathy, lumbar region",
                             is_primary=True)],
        prescriptions=[Prescription(prescription_id="r1", encounter_id="e1",
                                    patient_id="6027418", drug_name="meloxicam",
                                    strength="15", strength_unit="mg",
                                    sig_text="Take 1 po qd", quantity=30.0, refills=1)],
        medications=[MedicationSnapshot(encounter_id="e1", patient_id="6027418",
                                        medication_name="omeprazole", route="Oral")],
        imaging=[ImagingStudy(imaging_id="i1", encounter_id="e1", patient_id="6027418",
                              modality="XR", body_part="lumbar spine",
                              performed_date=date(2025, 4, 2))],
    )
    base.update(overrides)
    return ExtractedDocument(
        document=Document(document_id="d1", gcs_uri="gs://b/c.pdf", file_name="c.pdf",
                          file_bytes=1, page_count=3, ingest_run_id="r1"),
        **base,
    )


def by_field(doc=None, spec=None, **kwargs):
    return {r.field: r for r in compare_document(doc or extracted(), spec or truth(),
                                                 **kwargs)}


def test_a_perfect_extraction_scores_1_0():
    results = compare_document(extracted(), truth())
    assert all(r.accuracy == 1.0 for r in results), [r for r in results if r.accuracy < 1.0]


def test_a_wrong_mrn_is_counted_against_the_mrn_field_only():
    doc = extracted()
    doc.patient = doc.patient.model_copy(update={"mrn": "6027419"})
    scored = by_field(doc)
    assert scored["patient.mrn"].accuracy == 0.0
    assert scored["patient.family_name"].accuracy == 1.0


def test_sex_matches_across_the_two_spellings():
    spec = truth()
    spec.patient.sex = "Female"                                  # as a chart writes it
    doc = extracted()
    doc.patient = doc.patient.model_copy(update={"sex": "F"})    # as the warehouse stores it
    assert by_field(doc, spec)["patient.sex"].accuracy == 1.0


def test_a_region_with_no_side_matches_a_null_laterality():
    spec = truth()
    spec.encounters[0].laterality = "none"
    doc = extracted()
    doc.encounters = [doc.encounters[0].model_copy(update={"laterality": None})]
    assert by_field(doc, spec)["encounter.laterality"].accuracy == 1.0


def test_a_split_vocabulary_region_is_not_scored_as_a_miss():
    """The corpus writes "hand/wrist"; the classifier's vocabulary splits it."""
    spec = truth()
    spec.encounters[0].body_region = "hand/wrist"
    doc = extracted()
    doc.encounters = [doc.encounters[0].model_copy(update={"body_region": "wrist"})]
    assert by_field(doc, spec)["encounter.body_region"].accuracy == 1.0


def test_follow_up_is_unscored_when_the_spec_declares_no_expected_value():
    spec = truth()
    spec.encounters[0].follow_up_days = None
    assert "encounter.follow_up_interval_days" not in by_field(spec=spec)


def test_a_missing_prescription_counts_as_a_miss_not_a_crash():
    scored = by_field(extracted(prescriptions=[]))
    assert scored["prescription.drug_name"].correct == 0
    assert scored["prescription.drug_name"].total == 1
    assert "meloxicam" in scored["prescription.drug_name"].misses[0]


def test_an_extra_diagnosis_does_not_inflate_the_score():
    doc = extracted()
    doc.diagnoses = doc.diagnoses + [
        Diagnosis(diagnosis_id="x2", encounter_id="e1", patient_id="6027418",
                  diagnosis_text="Hallucinated finding", icd10_code="M99.99")
    ]
    assert by_field(doc)["diagnosis.icd10_code"].accuracy < 1.0


def test_an_extra_medication_is_caught_by_the_count():
    doc = extracted()
    doc.medications = doc.medications + [
        MedicationSnapshot(encounter_id="e1", patient_id="6027418",
                           medication_name="invented", route="Oral")
    ]
    assert by_field(doc)["medication.count"].accuracy == 0.0


def test_a_vitals_row_invented_for_a_chart_with_none_is_a_miss():
    spec = truth()
    spec.encounters[0].vitals = None
    assert by_field(spec=spec)["vitals.absent"].accuracy == 0.0


def test_llm_fields_can_be_left_unscored():
    scored = by_field(include_llm=False)
    assert "encounter.body_region" not in scored
    assert "encounter.provider_name" in scored


def test_every_result_declares_its_extraction_method():
    for result in compare_document(extracted(), truth()):
        assert result.method in {"deterministic", "llm"}


def test_llm_derived_fields_are_marked_as_such():
    scored = by_field()
    assert scored["encounter.body_region"].method == "llm"
    assert scored["encounter.provider_name"].method == "deterministic"


def test_field_result_accuracy_is_safe_when_nothing_was_expected():
    assert FieldResult(field="x", method="deterministic", correct=0, total=0).accuracy is None


def test_overall_summarizes_one_method_at_a_time():
    scored = by_field()
    assert overall(scored, "deterministic") == 1.0
    assert overall(scored, "llm") == 1.0


def test_the_provided_chart_is_reported_separately_from_the_synthetic_corpus():
    scored = by_field()
    report = render_report(scored, sample=scored, llm_scored=True)
    assert "Provided chart" in report
    assert report.count("| Field | Correct | Total | Accuracy |") >= 3


def test_report_splits_the_table_by_method():
    report = render_report(by_field(), llm_scored=True)
    assert "Deterministic parsing" in report
    assert "LLM-derived" in report
    assert "patient.mrn" in report


def test_report_says_plainly_when_the_llm_columns_were_not_scored():
    """An unscored column has to be named as unscored. Reporting it as 0%
    would publish a number that measures a missing API key."""
    report = render_report(by_field(include_llm=False))
    assert "unscored in this run" in report
    assert "— LLM-derived —" not in report
