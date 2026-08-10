"""The two facts the brief points at that are not diagnoses or prescriptions.

The left rail is named in §5.1 as "longitudinal context that is true of the
patient rather than of the visit", and §5.2 requires the corpus to carry an
operative note. Both land at their own grain.
"""

from datetime import date

from ingestion.extract.fields.history import parse_history
from ingestion.extract.fields.procedures import parse_procedures
from ingestion.extract.layout import Block


def rail(*lines, x0=35.3):
    return [Block(text=text, x0=x0, y0=100 + i * 12, x1=x0 + 100, y1=110 + i * 12, page=1)
            for i, text in enumerate(lines)]


def body(*lines):
    return [Block(text=text, x0=35.3, y0=100 + i * 12, x1=500, y1=110 + i * 12, page=3)
            for i, text in enumerate(lines)]


# --- history -----------------------------------------------------------------

def test_each_rail_section_lands_under_its_own_history_type():
    found = parse_history(rail(
        "Medications", "nebivolol Oral",
        "Medical History", "Essential hypertension",
        "Musculoskeletal History", "Osteoarthritis, bilateral knees",
        "Musculoskeletal Family History", "Mother with knee replacement",
        "Musculoskeletal Surgery", "Right knee arthroscopy",
        "Surgical History", "Cholecystectomy",
        "Social History", "Smoking status - Never smoker",
    ))
    assert [(f.history_type, f.item_text) for f in found] == [
        ("medical", "Essential hypertension"),
        ("musculoskeletal", "Osteoarthritis, bilateral knees"),
        ("family", "Mother with knee replacement"),
        ("musculoskeletal_surgery", "Right knee arthroscopy"),
        ("surgical", "Cholecystectomy"),
        ("social", "Smoking status - Never smoker"),
    ]


def test_orthopedic_surgery_is_not_folded_into_general_surgical_history():
    """The source system separates them, which §5.1 calls a design decision to
    treat as signal."""
    found = parse_history(rail(
        "Musculoskeletal Surgery", "Left rotator cuff repair",
        "Surgical History", "Appendectomy",
    ))
    assert {f.history_type for f in found} == {"musculoskeletal_surgery", "surgical"}


def test_a_heading_wrapped_across_two_lines_still_names_one_section():
    """The provided chart's narrow rail breaks "Musculoskeletal / Family
    History"; a wider rail breaks "Musculoskeletal Family / History". Read
    separately, the content files under whichever half came last."""
    narrow = parse_history(rail("Musculoskeletal", "Family History", "Mother with arthritis"))
    wide = parse_history(rail("Musculoskeletal Family", "History", "Mother with arthritis"))
    assert [(f.history_type, f.item_text) for f in narrow] == [("family", "Mother with arthritis")]
    assert narrow == wide


def test_none_is_an_absence_not_a_history_item():
    assert parse_history(rail("Medical History", "None",
                              "Surgical History", "No Known Surgical History")) == []


def test_the_medication_list_is_not_history():
    """It sits in the same rail but changes between visits, so it is captured
    per encounter instead."""
    found = parse_history(rail("Medications", "nebivolol Oral", "Medical History", "Asthma"))
    assert [f.item_text for f in found] == ["Asthma"]


def test_the_same_item_printed_at_every_visit_yields_one_fact():
    found = parse_history(rail("Medical History", "Asthma", "Medical History", "Asthma"))
    assert len(found) == 1


def test_no_sidebar_yields_no_history():
    assert parse_history([]) == []


# --- procedures --------------------------------------------------------------

def test_an_operative_note_yields_a_procedure_with_its_own_date():
    """The operation happened between two clinic visits. Dating it to the
    encounter that reports it would put the wrong date on the only surgical
    fact in the warehouse."""
    found = parse_procedures(body(
        "Procedure: Left L5-S1 microdiscectomy",
        "Date:  07/02/2025",
        "Surgeon: Dorian Vance, MD",
        "Fragment of extruded disc material removed from the left lateral recess.",
    ), encounter_date=date(2025, 7, 16))
    assert len(found) == 1
    procedure = found[0]
    assert procedure.procedure_name == "Left L5-S1 microdiscectomy"
    assert procedure.performed_date == date(2025, 7, 2)
    assert procedure.laterality == "left"
    assert procedure.surgeon_name == "Dorian Vance, MD"
    assert "extruded disc material" in procedure.note_text


def test_a_spinal_level_names_the_region_the_procedure_name_does_not():
    found = parse_procedures(body("Procedure: Left L5-S1 microdiscectomy",
                                  "Date:  07/02/2025"),
                             encounter_date=date(2025, 7, 16))
    assert found[0].body_part == "lumbar spine"
    cervical = parse_procedures(body("Procedure: C5-C6 anterior discectomy and fusion"),
                                encounter_date=date(2025, 7, 16))
    assert cervical[0].body_part == "cervical spine"


def test_the_surgeon_field_does_not_swallow_the_narrative():
    """PDF extraction collapses the line break after the name; a greedy match
    files the whole operative note as the surgeon's name."""
    found = parse_procedures(body(
        "Procedure: Right knee arthroscopy", "Surgeon: Marla Whitcomb, NP",
        "Partial medial meniscectomy performed. No complications.",
    ), encounter_date=date(2025, 7, 16))
    assert found[0].surgeon_name == "Marla Whitcomb, NP"
    assert "meniscectomy" in found[0].note_text


def test_a_procedure_with_no_printed_date_falls_back_to_the_encounter():
    found = parse_procedures(body("Procedure: Right knee arthroscopy"),
                             encounter_date=date(2025, 7, 16))
    assert found[0].performed_date == date(2025, 7, 16)


def test_prose_with_no_labelled_procedure_yields_nothing():
    assert parse_procedures(body("The patient is recovering well after surgery."),
                            encounter_date=date(2025, 7, 16)) == []


def test_no_operative_note_yields_nothing():
    assert parse_procedures([], encounter_date=date(2025, 7, 16)) == []
