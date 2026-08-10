"""The provided chart, end to end.

This is the test that keeps the project honest. Seven of the eight charts were
rendered from templates written here; this one came out of a real EMR and the
parser has never been allowed to see its layout as a design input. Every
assertion below is a fact printed on the page.
"""

from datetime import date, datetime

import pytest

from ingestion.extract.pipeline import extract_document

pytestmark = pytest.mark.golden

SAMPLE_NAME = ("EMA_20250723T140400_0000_MRN4820917_PMS4820917"
               "_PID18442091_PatientChart_400112.pdf")


@pytest.fixture(scope="module")
def doc(sample_pdf_bytes, module_cfg):
    return extract_document(
        sample_pdf_bytes, file_name=SAMPLE_NAME, cfg=module_cfg,
        llm_client=None,  # LLM columns stay NULL; deterministic fields must still land
    )


def test_patient_identity(doc):
    p = doc.patient
    assert p.mrn == "4820917"
    assert p.pms_id == "4820917"
    assert p.family_name == "BARLOW"
    assert p.given_name == "TREMAINE"
    assert p.preferred_name == "Trey Barlow"
    assert p.legal_name == "BARLOW, TREMAINE (Trey Barlow)"
    assert p.date_of_birth == date(1991, 9, 15)
    assert p.sex == "M"
    assert p.phone_home == "(615) 555-0173"
    assert p.first_seen_date == date(2025, 7, 23)
    assert p.last_seen_date == date(2025, 8, 13)


def test_two_encounters_with_the_right_page_ranges(doc):
    assert [e.encounter_date for e in doc.encounters] == [date(2025, 7, 23), date(2025, 8, 13)]
    assert (doc.encounters[0].source_page_start, doc.encounters[0].source_page_end) == (1, 3)
    assert (doc.encounters[1].source_page_start, doc.encounters[1].source_page_end) == (4, 5)
    assert [e.encounter_seq for e in doc.encounters] == [1, 2]
    assert all(e.patient_id == doc.patient.patient_id for e in doc.encounters)


def test_provider_location_and_signature_are_captured(doc):
    first = doc.encounters[0]
    assert first.provider_name == "Marla Whitcomb"
    assert first.provider_role == "NP"
    assert first.is_primary_provider is True
    assert first.location_name == "Cumberland Brentwood"
    assert first.signed_by == "Marla Whitcomb, NP"
    assert first.signed_at == datetime(2025, 7, 23, 14, 37)


def test_follow_up_intervals_normalize_to_days(doc):
    assert [e.follow_up_interval_days for e in doc.encounters] == [21, 28]
    assert doc.encounters[0].follow_up_raw == "Follow up in 3 weeks"


def test_the_shoulder_diagnosis_lands_with_its_code(doc):
    first = doc.encounters[0].encounter_id
    codes = {d.icd10_code for d in doc.diagnoses if d.encounter_id == first}
    assert "M25.511" in codes
    primary = next(d for d in doc.diagnoses if d.icd10_code == "M25.511")
    assert primary.body_region == "shoulder"
    assert primary.laterality == "right"
    assert primary.is_primary is True


def test_the_problem_title_is_not_counted_as_a_third_diagnosis(doc):
    first = doc.encounters[0].encounter_id
    texts = [d.diagnosis_text for d in doc.diagnoses if d.encounter_id == first]
    assert texts == ["Pain in right shoulder", "Acromioclavicular Arthritis"]


def test_the_meloxicam_prescription_lands_intact(doc):
    rx = next(r for r in doc.prescriptions if r.drug_name == "meloxicam")
    assert rx.encounter_id == doc.encounters[0].encounter_id
    assert rx.strength == "15"
    assert rx.strength_unit == "mg"
    assert rx.dose_form == "tablet"
    assert rx.route == "PO"
    assert rx.quantity == 30.0
    assert rx.quantity_unit == "Tablet"
    assert rx.refills == 2
    assert rx.duration_days == 14
    assert rx.is_prn is True
    assert "don't take with ibuprofen or naproxen" in rx.sig_text


def test_a_prescribing_action_with_no_printed_dosing_is_recorded_not_invented(doc):
    """The August visit says only "Modify Regimen: Modify prescription
    medication therapy." Carrying July's dose across would put a dose the
    document never contained into the warehouse."""
    august = doc.encounters[1].encounter_id
    assert [r for r in doc.prescriptions if r.encounter_id == august] == []
    gaps = [i for i in doc.issues
            if i.encounter_id == august and i.field_name == "prescriptions"]
    assert len(gaps) == 1
    assert gaps[0].severity == "warn"


def test_sparse_vitals_land_without_shifting(doc):
    v = doc.vitals[0]
    assert v.encounter_id == doc.encounters[0].encounter_id
    assert v.height_in == 67.0
    assert v.weight_lbs == 273.2
    assert v.bmi == 42.8
    assert v.bsa == 2.3
    assert v.bp_systolic is None
    assert v.bp_diastolic is None
    assert v.pulse is None
    assert v.respirations is None
    assert v.o2_sat is None
    assert v.temperature_f is None
    assert v.is_patient_reported is True
    assert v.taken_by == "Ortega, Renata"


def test_the_second_visit_has_no_vitals_and_says_so(doc):
    august = doc.encounters[1].encounter_id
    assert [v for v in doc.vitals if v.encounter_id == august] == []
    assert any(i.encounter_id == august and i.field_name == "vitals"
               and i.issue_type == "missing_section" for i in doc.issues)


def test_the_medication_snapshot_differs_between_the_two_visits(doc):
    """Meloxicam is absent in July and present in August because it was
    prescribed in between. A patient-level medication list destroys this."""
    def meds(encounter):
        return {m.medication_name for m in doc.medications if m.encounter_id == encounter}

    july = meds(doc.encounters[0].encounter_id)
    august = meds(doc.encounters[1].encounter_id)
    assert july == {"nebivolol", "olmesartan-amlodipin-hcthiazid"}
    assert august == july | {"meloxicam"}


def test_the_xray_lands_as_one_study(doc):
    studies = [i for i in doc.imaging if i.encounter_id == doc.encounters[0].encounter_id]
    assert len(studies) == 1
    assert studies[0].modality == "XR"
    assert studies[0].body_part == "shoulder"
    assert studies[0].laterality == "right"
    assert studies[0].performed_date == date(2025, 7, 23)


def test_exam_findings_keep_the_two_columns_apart(doc):
    rom = [f for f in doc.exam_findings if f.finding_type == "rom_active"]
    assert {f.laterality for f in rom} == {"right", "left"}
    assert all(f.body_part == "shoulder" for f in rom)


def test_llm_columns_are_null_when_no_client_is_supplied(doc):
    assert all(e.body_region is None and e.hpi_summary is None and e.llm_model is None
               for e in doc.encounters)


def test_no_error_severity_issues_on_the_golden_chart(doc):
    assert [(i.issue_type, i.detail) for i in doc.issues if i.severity == "error"] == []
    assert doc.document.parse_status == "ok"


def test_document_row_describes_the_file(doc):
    assert doc.document.page_count == 5
    assert doc.document.file_bytes > 0
    assert len(doc.document.document_id) == 64
    assert doc.document.mrn_from_filename == "4820917"
    assert doc.document.pms_id_from_filename == "4820917"
