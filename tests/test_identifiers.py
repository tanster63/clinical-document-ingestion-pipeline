from datetime import date

from ingestion.extract.fields.identifiers import parse_filename_ids, parse_identity
from ingestion.extract.layout import Block, load_pages

SAMPLE_FILE = ("EMA_20250723T140400_0000_MRN4820917_PMS4820917"
               "_PID18442091_PatientChart_400112.pdf")


def blk(text, x0, y0, width=200):
    return Block(text=text, x0=x0, y0=y0, x1=x0 + width, y1=y0 + 10, page=1)


def header(*lines):
    return [blk(text, 40, 20 + i * 12) for i, text in enumerate(lines)]


def test_parse_filename_ids():
    assert parse_filename_ids(SAMPLE_FILE) == ("4820917", "4820917")
    assert parse_filename_ids("not-a-chart.pdf") == (None, None)


def test_parse_identity_reads_every_header_field_written_inline():
    identity, issues = parse_identity(
        header(
            "BARLOW, TREMAINE (Trey Barlow)",
            "DOB: 09/15/1991 Sex: Male MRN: 4820917 PMS ID: 4820917",
            "Home: (615) 555-0173",
        ),
        SAMPLE_FILE,
    )
    assert identity.mrn == "4820917"
    assert identity.pms_id == "4820917"
    assert identity.family_name == "BARLOW"
    assert identity.given_name == "TREMAINE"
    assert identity.preferred_name == "Trey Barlow"
    assert identity.legal_name == "BARLOW, TREMAINE (Trey Barlow)"
    assert identity.date_of_birth == date(1991, 9, 15)
    assert identity.sex == "M"
    assert identity.phone_home == "(615) 555-0173"
    assert issues == []


def test_labels_and_values_on_separate_rows_are_paired_by_column():
    """The provided chart's header is a table: labels on one row, values
    right-aligned beneath them. Read in reading order the text says
    "... MRN: 4820917 Male ..." where that 4820917 is the *PMS ID*, so a text
    regex reads the wrong cell. Geometry reads the right one."""
    labels = [
        blk("PMS ID:", 390.5, 52.5, 22.3), blk("Sex:", 423.1, 52.5, 12.0),
        blk("DOB:", 465.5, 52.5, 14.7), blk("Phone:", 520.8, 52.5, 19.1),
        blk("MRN:", 560.7, 52.5, 15.3),
    ]
    values = [
        blk("1111111", 381.7, 60.4, 31.1), blk("Male", 417.8, 60.4, 17.3),
        blk("09/15/1991", 440.1, 60.4, 40.1), blk("(615) 555-0173", 485.2, 60.4, 54.7),
        blk("2222222", 544.9, 60.4, 31.1),
    ]
    identity, _ = parse_identity(labels + values, "chart.pdf")
    assert identity.mrn == "2222222"
    assert identity.pms_id == "1111111"
    assert identity.sex == "M"
    assert identity.date_of_birth == date(1991, 9, 15)
    assert identity.phone_home == "(615) 555-0173"


def test_name_without_a_preferred_form_parses():
    identity, _ = parse_identity(header("GRISWOLD, ANNETTE", "MRN: 5193064"), SAMPLE_FILE)
    assert identity.family_name == "GRISWOLD"
    assert identity.given_name == "ANNETTE"
    assert identity.preferred_name is None


def test_missing_phone_is_not_an_error():
    identity, issues = parse_identity(header("NAKAGAWA, HIROSHI", "MRN: 8210377"), SAMPLE_FILE)
    assert identity.phone_home is None
    assert not [i for i in issues if i.severity == "error"]


def test_filename_mrn_mismatch_records_an_issue_and_trusts_the_header():
    identity, issues = parse_identity(header("X, Y", "MRN: 9999999"), SAMPLE_FILE)
    assert identity.mrn == "9999999"
    assert "identifier_mismatch" in {i.issue_type for i in issues}
    assert any("4820917" in i.detail for i in issues)


def test_missing_mrn_in_header_falls_back_to_the_filename_with_a_warning():
    identity, issues = parse_identity(header("X, Y", "no identifiers here"), SAMPLE_FILE)
    assert identity.mrn == "4820917"
    assert any(i.issue_type == "unparsed_field" and i.field_name == "mrn" for i in issues)


def test_the_provided_chart_reads_correctly_end_to_end(sample_pdf_bytes):
    identity, issues = parse_identity(load_pages(sample_pdf_bytes)[0].header, SAMPLE_FILE)
    assert identity.mrn == "4820917"
    assert identity.pms_id == "4820917"
    assert identity.preferred_name == "Trey Barlow"
    assert identity.date_of_birth == date(1991, 9, 15)
    assert identity.sex == "M"
    assert identity.phone_home == "(615) 555-0173"
    assert issues == []
