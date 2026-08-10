from ingestion.extract.fields.exam import parse_exam_findings
from ingestion.extract.layout import Block


def line(text, x0, y0, width=200, page=2):
    return Block(text=text, x0=x0, y0=y0, x1=x0 + width, y1=y0 + 10, page=page)


def test_two_columns_keep_their_own_side():
    """The provided chart prints right and left findings side by side. Read in
    plain reading order the columns interleave and every left-side measurement
    is filed under the right shoulder — the worst error this parser can make,
    because it reports the wrong side of the body."""
    found = parse_exam_findings([
        line("Right Shoulder Active ROM:", 154, 100), line("Left Shoulder Active ROM:", 377, 100),
        line("Forward Flexion: 180 degrees.", 154, 116),
        line("Forward Flexion: 170 degrees.", 377, 116),
    ])
    by_side = {(f.laterality, f.measure_name): f.value_numeric for f in found}
    assert by_side[("right", "Forward Flexion")] == 180.0
    assert by_side[("left", "Forward Flexion")] == 170.0


def test_a_measurement_carries_its_number_and_unit():
    found = parse_exam_findings([
        line("Right Shoulder Active ROM:", 154, 100),
        line("Abduction: 180 degrees.", 154, 116),
    ])
    assert found[0].finding_type == "rom_active"
    assert found[0].body_part == "shoulder"
    assert found[0].value_numeric == 180.0
    assert found[0].unit == "degrees"
    assert "180" in found[0].value_text


def test_the_subsection_heading_types_the_finding():
    found = parse_exam_findings([
        line("Skin:", 49, 100, 40),
        line("Right Shoulder: skin intact, no rashes or lesions.", 49, 114, 300),
        line("Strength:", 49, 140, 40),
        line("Right Shoulder Forward Flexion: Strength: 5/5, normal", 49, 154, 300),
    ])
    assert [f.finding_type for f in found] == ["skin", "strength"]
    assert all(f.laterality == "right" and f.body_part == "shoulder" for f in found)


def test_passive_and_active_range_of_motion_are_different_types():
    found = parse_exam_findings([
        line("Right Knee Active ROM:", 154, 100),
        line("Flexion: 125 degrees.", 154, 116),
        line("Right Knee Passive ROM:", 154, 140),
        line("Flexion: 130 degrees.", 154, 156),
    ])
    assert [f.finding_type for f in found] == ["rom_active", "rom_passive"]
    assert [f.value_numeric for f in found] == [125.0, 130.0]


def test_a_wrapped_line_is_joined_rather_than_becoming_its_own_finding():
    found = parse_exam_findings([
        line("Strength:", 49, 100, 40),
        line("Right Shoulder External Rotation: Strength: 5/5, normal", 49, 114, 300),
        line("muscle tone.", 49, 128, 60),
    ])
    assert len(found) == 1
    assert found[0].value_text.endswith("muscle tone")


def test_prose_exams_are_typed_narrative_not_forced_into_a_measurement():
    found = parse_exam_findings([
        line("Right hip: antalgic gait, decreased internal rotation, positive FABER test.",
             186, 100, 400),
    ])
    assert found[0].finding_type == "narrative"
    assert found[0].body_part == "hip"
    assert found[0].laterality == "right"
    assert found[0].value_numeric is None
    assert "FABER" in found[0].value_text


def test_no_exam_section_returns_nothing():
    assert parse_exam_findings([]) == []
