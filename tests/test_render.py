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
    """The parser's geometry assumption (Task 5) must hold in generated charts."""
    _, path = rendered
    with fitz.open(path) as doc:
        page = doc[0]
        sidebar_hits = page.search_for("Current Medications")
        body_hits = page.search_for("Chief Complaint")
    assert sidebar_hits and body_hits
    assert sidebar_hits[0].x1 < body_hits[0].x0


def test_every_prescription_appears_in_the_text(rendered):
    spec, path = rendered
    with fitz.open(path) as doc:
        text = "".join(page.get_text() for page in doc)
    for enc in spec.encounters:
        for rx in enc.prescriptions:
            assert rx.drug_name in text
            assert rx.sig_text[:20] in text
