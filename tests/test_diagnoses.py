from ingestion.extract.fields.diagnoses import (
    body_region_from_icd10, laterality_from_icd10, parse_diagnoses,
)
from ingestion.extract.layout import Block


def blk(text, y0, x0=260):
    return Block(text=text, x0=x0, y0=y0, x1=x0 + 300, y1=y0 + 10, page=2)


def test_body_region_from_icd10():
    assert body_region_from_icd10("M25.511") == "shoulder"
    assert body_region_from_icd10("M17.11") == "knee"
    assert body_region_from_icd10("M16.11") == "hip"
    assert body_region_from_icd10("M77.11") == "elbow"
    assert body_region_from_icd10("M65.4") == "wrist"
    assert body_region_from_icd10("M72.2") == "foot"
    assert body_region_from_icd10("M51.16") == "lumbar spine"
    assert body_region_from_icd10("M50.122") == "cervical spine"
    assert body_region_from_icd10("Z00.00") is None


def test_laterality_only_where_the_code_actually_encodes_it():
    assert laterality_from_icd10("M25.511") == "right"
    assert laterality_from_icd10("M25.512") == "left"
    assert laterality_from_icd10("M25.519") is None
    assert laterality_from_icd10("M17.11") == "right"
    assert laterality_from_icd10("M79.672") == "left"
    # M50.122's trailing digit is a spinal level, not a side.
    assert laterality_from_icd10("M50.122") is None
    assert laterality_from_icd10("M65.4") is None


def test_parse_diagnoses_extracts_code_text_and_primary_flag():
    found = parse_diagnoses([
        blk("Primary osteoarthritis of right knee (M17.11) [Primary]", 100),
        blk("Chondromalacia patellae (M22.41)", 112),
    ])
    assert len(found) == 2
    assert found[0].icd10_code == "M17.11"
    assert found[0].diagnosis_text == "Primary osteoarthritis of right knee"
    assert found[0].is_primary is True
    assert found[0].body_region == "knee"
    assert found[0].laterality == "right"
    assert found[1].is_primary is False


def test_a_primary_marker_that_wrapped_onto_its_own_line_still_counts():
    """A long diagnosis pushes "[Primary]" onto the next line. Without this the
    marker is orphaned and no diagnosis is flagged primary at all."""
    found = parse_diagnoses([
        blk("Lumbar disc herniation with left-sided radiculopathy (M51.16)", 100),
        blk("[Primary]", 112),
        blk("Lumbar radiculopathy, improving (M54.16)", 124),
    ])
    assert len(found) == 2
    assert found[0].is_primary is True
    assert found[1].is_primary is False


def test_first_diagnosis_is_primary_when_nothing_is_flagged():
    found = parse_diagnoses([blk("Pain in right shoulder (M25.511)", 100),
                             blk("Acromioclavicular arthritis", 112)])
    assert found[0].is_primary is True
    assert found[1].is_primary is False


def test_a_numbered_problem_elaborated_by_a_coded_line_is_not_double_counted():
    """The provided chart prints the problem, then the coded diagnosis under
    it. Emitting both would report two diagnoses where there is one."""
    found = parse_diagnoses([
        blk("1. Shoulder Pain, Right", 100),
        blk("Pain in right shoulder (M25.511)", 112),
        blk("Associated diagnosis: Acromioclavicular Arthritis", 124),
    ])
    assert [(d.icd10_code, d.diagnosis_text) for d in found] == [
        ("M25.511", "Pain in right shoulder"),
        (None, "Acromioclavicular Arthritis"),
    ]
    assert found[0].is_primary is True


def test_a_numbered_problem_with_no_coded_line_beneath_it_is_kept():
    found = parse_diagnoses([blk("1. Shoulder Pain, Right", 100),
                             blk("2. Cervicalgia", 112)])
    assert [d.diagnosis_text for d in found] == ["Shoulder Pain, Right", "Cervicalgia"]


def test_a_bullet_split_into_its_own_block_does_not_become_a_diagnosis():
    found = parse_diagnoses([
        Block(text="•", x0=210, y0=100, x1=216, y1=110, page=2),
        Block(text="Severe right hip osteoarthritis (M16.11) [Primary]",
              x0=216, y0=100, x1=398, y1=110, page=2),
    ])
    assert len(found) == 1
    assert found[0].icd10_code == "M16.11"


def test_diagnosis_without_a_code_still_lands():
    found = parse_diagnoses([blk("Acromioclavicular arthritis", 100)])
    assert found[0].icd10_code is None
    assert found[0].diagnosis_text == "Acromioclavicular arthritis"
    assert found[0].body_region is None


def test_source_is_recorded():
    found = parse_diagnoses([blk("Rotator cuff tendinopathy", 100)], source="imaging")
    assert found[0].source == "imaging"
    assert found[0].source_page == 2


def test_empty_section_returns_empty_list():
    assert parse_diagnoses([]) == []
