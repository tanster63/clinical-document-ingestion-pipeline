from datetime import date

from ingestion.extract.fields.followup import parse_follow_up
from ingestion.extract.fields.imaging import parse_imaging
from ingestion.extract.fields.medications import parse_medications
from ingestion.extract.fields.vitals import parse_vitals
from ingestion.extract.layout import Block


def cell(text, x0, y0, width=40):
    return Block(text=text, x0=x0, y0=y0, x1=x0 + width, y1=y0 + 9, page=1)


# --- vitals ------------------------------------------------------------------

def test_vitals_pairs_labels_to_values_by_column():
    blocks = [
        cell("BP", 260, 100), cell("Pulse", 320, 100), cell("Ht (in)", 380, 100),
        cell("Wt (lbs)", 440, 100), cell("BMI", 500, 100),
        cell("132/84", 260, 112), cell("76", 320, 112), cell("64.0", 380, 112),
        cell("181.4", 440, 112), cell("31.1", 500, 112),
    ]
    v = parse_vitals(blocks)
    assert (v.bp_systolic, v.bp_diastolic) == (132, 84)
    assert v.pulse == 76
    assert v.height_in == 64.0
    assert v.weight_lbs == 181.4
    assert v.bmi == 31.1
    assert v.o2_sat is None


def test_vitals_with_blank_cells_does_not_shift_values():
    """The provided chart's case: only Ht, Wt, BMI and BSA are filled in. A
    blank cell emits no block at all, so anything reading by column position
    slides those four values under BP, Pulse, Resp and O2."""
    blocks = [
        cell("B.P.", 280, 100, 15), cell("Pulse", 314, 100, 21),
        cell("Resp.", 357, 100, 22), cell("O2 Sat.", 398, 100, 28),
        cell("Temp.", 435, 100, 24), cell("Ht.", 476, 100, 11),
        cell("Wt.", 510, 100, 13), cell("BMI BSA", 537, 100, 38),
        cell("67.0 in", 466, 112, 24), cell("273.2", 501, 112, 20),
        cell("42.8 2.3", 536, 112, 33),
    ]
    v = parse_vitals(blocks)
    assert v.height_in == 67.0
    assert v.weight_lbs == 273.2
    assert v.bmi == 42.8          # from the merged "BMI BSA" header cell
    assert v.bsa == 2.3
    assert v.bp_systolic is None
    assert v.pulse is None
    assert v.respirations is None
    assert v.o2_sat is None
    assert v.temperature_f is None


def test_vitals_reads_who_took_them_and_when():
    blocks = [
        cell("Date", 167, 100, 18), cell("Taken By", 215, 100, 36),
        cell("Ortega, Renata", 200, 112, 55), cell("07/23/25", 161, 118, 31),
    ]
    v = parse_vitals(blocks)
    assert v.taken_by == "Ortega, Renata"
    assert v.taken_date == date(2025, 7, 23)


def test_vitals_falls_back_to_inline_labelled_text():
    blocks = [cell("Ht 67.0 in  Wt 273.2 lbs  BMI 42.8  BSA 2.3", 260, 100, 300)]
    v = parse_vitals(blocks)
    assert (v.height_in, v.weight_lbs, v.bmi, v.bsa) == (67.0, 273.2, 42.8, 2.3)


def test_patient_reported_marker_is_captured():
    blocks = [cell("Ht (in)", 380, 100), cell("67.0", 380, 112),
              cell("* Patient Reported", 260, 130)]
    assert parse_vitals(blocks).is_patient_reported is True


def test_absent_vitals_section_returns_none():
    assert parse_vitals([]) is None


# --- imaging -----------------------------------------------------------------

def test_parse_imaging_reads_modality_part_side_and_date():
    blocks = [
        cell("XR knee (right) — performed 06/11/2025", 260, 100, 200),
        cell("Three views of the right knee. Medial joint space narrowing.", 260, 112, 300),
        cell("Impression: Moderate medial compartment osteoarthritis, right knee.",
             260, 124, 300),
    ]
    studies = parse_imaging(blocks, encounter_date=date(2025, 6, 11))
    assert len(studies) == 1
    im = studies[0]
    assert im.modality == "XR"
    assert im.body_part == "knee"
    assert im.laterality == "right"
    assert im.performed_date == date(2025, 6, 11)
    assert "Medial joint space narrowing" in im.interpretation_text
    assert im.impression.startswith("Moderate medial compartment")


def test_two_studies_in_one_section_stay_separate():
    blocks = [
        cell("XR lumbar spine — performed 05/14/2025", 260, 100, 200),
        cell("Impression: Degenerative changes at L5-S1.", 260, 112, 300),
        cell("MRI lumbar spine — performed 05/20/2025", 260, 130, 200),
        cell("Impression: Left paracentral disc extrusion at L5-S1.", 260, 142, 300),
    ]
    studies = parse_imaging(blocks, encounter_date=date(2025, 5, 14))
    assert [s.modality for s in studies] == ["XR", "MRI"]
    assert studies[1].performed_date == date(2025, 5, 20)


def test_a_side_described_in_the_findings_is_not_the_studys_laterality():
    """An MRI of the lumbar spine reporting a left-sided herniation is not a
    left-sided study; the region has no side."""
    blocks = [
        cell("MRI lumbar spine — performed 05/20/2025", 260, 100, 200),
        cell("MRI of the lumbar spine without contrast. Left paracentral disc "
             "herniation at L5-S1.", 260, 112, 400),
        cell("Impression: Left L5-S1 disc herniation.", 260, 124, 300),
    ]
    assert parse_imaging(blocks, encounter_date=date(2025, 5, 20))[0].laterality is None


def test_one_study_written_as_several_modality_paragraphs_stays_one_study():
    """The provided chart repeats "X-Ray" at the head of three paragraphs that
    all describe the same shoulder film."""
    blocks = [
        cell("X-Ray Interpretation Shoulder", 35, 100, 150),
        cell("Diagnosis: Shoulder Pain, Right - M25.511", 35, 112, 250),
        cell("X-Ray Data:", 35, 124, 60),
        cell("Date:  07/23/2025", 35, 136, 80),
        cell("X-rays of the right shoulder were ordered and obtained: "
             "There is no fracture.", 35, 148, 400),
    ]
    studies = parse_imaging(blocks, encounter_date=date(2025, 7, 23))
    assert len(studies) == 1
    assert studies[0].modality == "XR"
    assert studies[0].body_part == "shoulder"
    assert studies[0].laterality == "right"
    assert studies[0].performed_date == date(2025, 7, 23)


def test_imaging_without_a_date_inherits_the_encounter_date():
    studies = parse_imaging([cell("XR right wrist 3 views", 260, 100, 150)],
                            encounter_date=date(2025, 7, 2))
    assert studies[0].performed_date == date(2025, 7, 2)


def test_no_imaging_section_returns_empty():
    assert parse_imaging([], encounter_date=date(2025, 7, 2)) == []


# --- follow-up ---------------------------------------------------------------

def test_follow_up_normalizes_to_days():
    assert parse_follow_up("Follow up in 3 weeks") == (21, "Follow up in 3 weeks")
    assert parse_follow_up("Follow up in 4 weeks") == (28, "Follow up in 4 weeks")
    assert parse_follow_up("Follow up in 10 days")[0] == 10
    assert parse_follow_up("Follow up in 3 months")[0] == 90
    assert parse_follow_up("Return to clinic in one year")[0] == 365


def test_follow_up_handles_spelled_out_numbers():
    assert parse_follow_up("Follow up in three weeks")[0] == 21


def test_follow_up_finds_the_phrase_inside_a_whole_plan_section():
    plan = ("Prescription. meloxicam 15 mg tablet PO Sig: Take 1 po qd x 2 weeks "
            "then PRN Quantity: 30 Tablet Refills: 2 Follow up in 3 weeks")
    assert parse_follow_up(plan) == (21, "Follow up in 3 weeks")


def test_follow_up_absent_returns_nones():
    assert parse_follow_up("Return as needed") == (None, "Return as needed")
    assert parse_follow_up("") == (None, None)


def test_a_long_section_with_no_follow_up_phrase_stores_no_raw_text():
    assert parse_follow_up("x" * 400) == (None, None)


# --- medication snapshots ----------------------------------------------------

def test_medications_read_from_the_sidebar():
    blocks = [
        cell("Current Medications", 30, 100, 100),
        cell("nebivolol — Oral", 30, 112, 100),
        cell("olmesartan-amlodipin-hcthiazid — Oral", 30, 124, 130),
        cell("Allergies", 30, 150, 60),
        cell("No Known Drug Allergies", 30, 162, 110),
    ]
    meds = parse_medications(blocks)
    assert [m.medication_name for m in meds] == ["nebivolol", "olmesartan-amlodipin-hcthiazid"]
    assert all(m.route == "Oral" for m in meds)


def test_strength_and_dose_form_are_stripped_from_the_name():
    """The provided chart writes "meloxicam 15 mg Oral - tablet" in the rail."""
    blocks = [cell("Medications", 30, 100, 60),
              cell("meloxicam 15 mg Oral - tablet", 30, 112, 120)]
    meds = parse_medications(blocks)
    assert meds[0].medication_name == "meloxicam"
    assert meds[0].route == "Oral"


def test_medications_none_recorded_is_not_a_medication():
    blocks = [cell("Current Medications", 30, 100, 100), cell("None recorded", 30, 112, 60)]
    assert parse_medications(blocks) == []


def test_a_history_heading_stops_the_list():
    blocks = [
        cell("Medications", 30, 100, 60), cell("nebivolol Oral", 30, 112, 70),
        cell("Medical History", 30, 130, 70), cell("Essential hypertension", 30, 142, 100),
    ]
    assert [m.medication_name for m in parse_medications(blocks)] == ["nebivolol"]
