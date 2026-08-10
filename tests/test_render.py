import fitz
import pytest

from corpus.spec_model import load_spec

# WeasyPrint links against Pango and Cairo at import time, and reports a missing
# one as OSError rather than ImportError. Those are corpus-authoring
# dependencies, not runtime ones — nothing that gets deployed renders a PDF — so
# a machine without them skips this file rather than failing a suite it cannot
# satisfy. The README carries the one-line macOS fix.
try:
    from corpus.render import render_chart
except (ImportError, OSError) as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"WeasyPrint's native libraries are not loadable here ({exc}); "
        "see the README for the macOS DYLD_FALLBACK_LIBRARY_PATH note",
        allow_module_level=True,
    )

SPEC = "corpus/specs/chart_01.json"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    spec = load_spec(SPEC)
    return spec, render_chart(spec, tmp_path_factory.mktemp("charts"))


def test_renders_one_pdf_with_pages_for_every_encounter(rendered):
    spec, path = rendered
    with fitz.open(path) as doc:
        assert doc.page_count >= len(spec.encounters)


def test_header_repeats_identity_on_every_page(rendered):
    spec, path = rendered
    with fitz.open(path) as doc:
        for page in doc:
            assert spec.patient.mrn in page.get_text()


def test_page_counter_resets_per_encounter(rendered):
    spec, path = rendered
    with fitz.open(path) as doc:
        page_ones = sum("Page 1" in page.get_text() for page in doc)
    assert page_ones == len(spec.encounters)


def test_sidebar_sits_left_of_the_body_column(rendered):
    """The rail is a rail: the parser's geometry assumption has to hold on the
    authored charts as well as on the provided one."""
    _, path = rendered
    with fitz.open(path) as doc:
        page = doc[0]
        sidebar_hits = page.search_for("Medical History")
        body_hits = page.search_for("Chief Complaints:")
    assert sidebar_hits and body_hits
    assert sidebar_hits[0].x1 < body_hits[0].x0


def test_the_authored_chart_prints_the_same_section_labels_as_the_sample(rendered):
    """§5.2: the corpus must look as though it came out of the same system.
    These are the labels the provided export prints, verbatim."""
    _, path = rendered
    with fitz.open(path) as doc:
        text = "".join(page.get_text() for page in doc)
    for label in ("Chief Complaints:", "HPI: This is a", "Vitals:", "Exam:",
                  "Impression/Plan:", "Note:", "Staff:", "Electronically Signed By:",
                  "Medications", "Medical History", "Musculoskeletal Family",
                  "Surgical History", "Social History"):
        assert label in text, f"authored chart is missing the sample's {label!r}"


def test_the_page_counter_restarts_at_each_visit(rendered):
    """The provided chart resets to Page 1 at the encounter boundary, and that
    reset is one of the two signals the encounter splitter relies on."""
    spec, path = rendered
    with fitz.open(path) as doc:
        firsts = [n for n, page in enumerate(doc) if "Page 1" in page.get_text()]
    assert len(firsts) == len(spec.encounters)


def test_every_prescription_appears_in_the_text(rendered):
    spec, path = rendered
    with fitz.open(path) as doc:
        text = "".join(page.get_text() for page in doc)
    for enc in spec.encounters:
        for rx in enc.prescriptions:
            assert rx.drug_name in text
            assert rx.sig_text[:20] in text
