from ingestion.extract.layout import Block
from ingestion.extract.sections import (
    find_sections, normalize_heading, section_blocks, section_text, split_heading,
)


def blk(text, y0, x0=260):
    return Block(text=text, x0=x0, y0=y0, x1=x0 + 200, y1=y0 + 10, page=1)


def test_normalize_heading_maps_known_variants():
    assert normalize_heading("CHIEF COMPLAINT") == "chief_complaint"
    assert normalize_heading("History of Present Illness") == "hpi"
    assert normalize_heading("HPI:") == "hpi"
    assert normalize_heading("Assessment and Plan") == "assessment"
    assert normalize_heading("Impression") == "assessment"
    assert normalize_heading("Impression/Plan:") == "assessment"
    assert normalize_heading("Radiology") == "imaging"
    assert normalize_heading("Tests") == "imaging"
    assert normalize_heading("some sentence of prose that is long") is None


def test_a_heading_sharing_its_line_with_content_splits():
    """The provided chart writes "HPI: This is a 33 year old male who:" — the
    heading and the first sentence of its own content on one line."""
    key, rest = split_heading("HPI: This is a 33 year old male who:")
    assert key == "hpi"
    assert rest == "This is a 33 year old male who:"


def test_only_whitelisted_labels_may_split_a_line():
    """`Impression:` heads a section on its own line but labels a paragraph
    inside an imaging report. Splitting there would tear a study's impression
    out of the study."""
    assert split_heading("Impression: Severe right hip osteoarthritis.") == (
        None, "Impression: Severe right hip osteoarthritis."
    )
    assert split_heading("Sig: Take 1 po qd") == (None, "Sig: Take 1 po qd")
    assert split_heading("Date: 07/23/2025") == (None, "Date: 07/23/2025")


def test_find_sections_buckets_blocks_under_their_heading():
    blocks = [
        blk("CHIEF COMPLAINT", 100), blk("Right knee pain", 112),
        blk("VITALS", 130), blk("BP 132/84", 142),
        blk("ASSESSMENT", 160), blk("Osteoarthritis (M17.11)", 172),
    ]
    sections = find_sections(blocks)
    assert section_text(sections, "chief_complaint") == "Right knee pain"
    assert "132/84" in section_text(sections, "vitals")
    assert "M17.11" in section_text(sections, "assessment")


def test_an_inline_heading_keeps_its_content_and_its_geometry():
    sections = find_sections([blk("HPI: patient reports pain", 100)])
    kept = sections["hpi"][0]
    assert kept.text == "patient reports pain"
    assert (kept.x0, kept.y0) == (260, 100)


def test_a_repeated_heading_appends_rather_than_replacing():
    """The provided chart writes "Plan:" twice in one encounter."""
    sections = find_sections([
        blk("Plan: Prescription.", 100), blk("meloxicam 15 mg", 110),
        blk("Plan: Counseling.", 120), blk("handout provided", 130),
    ])
    text = section_text(sections, "plan")
    assert "meloxicam" in text and "handout" in text


def test_sidebar_headings_stop_the_medication_list_bleeding():
    """Without an alias for "Medical History", every later sidebar line reads
    as another medication."""
    sections = find_sections([
        blk("Medications", 100, x0=30), blk("nebivolol Oral", 110, x0=30),
        blk("Medical History", 120, x0=30), blk("Essential hypertension", 130, x0=30),
    ])
    assert [b.text for b in sections["current_medications"]] == ["nebivolol Oral"]
    assert "Essential hypertension" in section_text(sections, "medical_history")


def test_content_before_the_first_heading_is_kept_as_preamble():
    sections = find_sections([blk("stray text", 90), blk("VITALS", 100), blk("BP", 110)])
    assert section_text(sections, "_preamble") == "stray text"


def test_missing_section_returns_empty_not_keyerror():
    assert section_text(find_sections([blk("VITALS", 100)]), "imaging") == ""
    assert section_blocks(find_sections([blk("VITALS", 100)]), "imaging", "plan") == []


def test_section_blocks_returns_several_sections_in_reading_order():
    sections = find_sections([
        blk("PRESCRIPTIONS", 100), blk("first", 110),
        blk("PLAN", 120), blk("second", 130),
    ])
    combined = section_blocks(sections, "prescriptions", "plan")
    assert [b.text for b in combined] == ["first", "second"]
