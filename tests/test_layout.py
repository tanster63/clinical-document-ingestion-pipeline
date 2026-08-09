import fitz
import pytest

from ingestion.extract.layout import (
    Block, load_pages, page_label_of, reading_order, split_regions, text_of,
)


def block(text, x0, y0, x1=None, y1=None, page=1):
    return Block(text=text, x0=x0, y0=y0, x1=x1 if x1 is not None else x0 + 50,
                 y1=y1 if y1 is not None else y0 + 10, page=page)


def test_reading_order_sorts_top_to_bottom_then_left_to_right():
    blocks = [block("c", 300, 200), block("a", 40, 100), block("b", 300, 101)]
    assert [b.text for b in reading_order(blocks)] == ["a", "b", "c"]


def test_text_of_joins_in_reading_order():
    blocks = [block("world", 300, 100), block("hello", 40, 100)]
    assert text_of(blocks) == "hello world"


def test_split_regions_finds_the_gutter_from_whitespace():
    width, height = 612.0, 792.0
    blocks = (
        [block("MRN: 123", 40, 20), block("Patient Name", 40, 40)]          # header band
        + [block(f"side {i}", 30, 200 + i * 20, 120, 210 + i * 20) for i in range(6)]
        + [block(f"body {i}", 260, 200 + i * 20, 560, 210 + i * 20) for i in range(6)]
    )
    header, sidebar, body = split_regions(blocks, width, height)
    assert {b.text for b in header} == {"MRN: 123", "Patient Name"}
    assert all(b.text.startswith("side") for b in sidebar)
    assert all(b.text.startswith("body") for b in body)


def test_split_regions_survives_a_wider_page():
    """Same document scaled to A3-ish width must split the same way."""
    scale = 1.5
    width, height = 612.0 * scale, 792.0 * scale
    blocks = (
        [block("MRN: 123", 40 * scale, 20 * scale)]
        + [block(f"side {i}", 30 * scale, (200 + i * 20) * scale, 120 * scale,
                 (210 + i * 20) * scale) for i in range(6)]
        + [block(f"body {i}", 260 * scale, (200 + i * 20) * scale, 560 * scale,
                 (210 + i * 20) * scale) for i in range(6)]
    )
    _, sidebar, body = split_regions(blocks, width, height)
    assert len(sidebar) == 6 and len(body) == 6


def test_page_label_reads_the_footer_counter():
    assert page_label_of([block("Page 2 of 3", 40, 760)]) == 2
    assert page_label_of([block("no counter here", 40, 760)]) is None


def test_load_pages_on_the_provided_chart(sample_pdf_bytes):
    pages = load_pages(sample_pdf_bytes)
    assert len(pages) == 5
    for page in pages:
        assert "4820917" in text_of(page.header)          # header band repeats identity
        assert page.width > 0 and page.height > 0
    assert "shoulder" in text_of(pages[0].body).lower()
    assert any(page.sidebar for page in pages)


def test_load_pages_keeps_every_block(sample_pdf_bytes):
    """No block may be dropped by the region split."""
    pages = load_pages(sample_pdf_bytes)
    with fitz.open(stream=sample_pdf_bytes, filetype="pdf") as doc:
        for page, layout in zip(doc, pages):
            raw = len([l for b in page.get_text("dict")["blocks"]
                       if b.get("type") == 0 for l in b["lines"]])
            assert len(layout.header) + len(layout.sidebar) + len(layout.body) == raw


# --- Regression coverage added in the round-1 review fix ---------------------
#
# The tests above are the brief's own and must stay untouched. The tests below
# lock in the fixes for the four review findings: a hard depth ceiling on the
# header cut (finding 1/2/3) and a phantom sidebar manufactured on pages that
# have no genuine gutter (finding 4).


def test_header_cut_extends_past_the_old_fixed_search_window_for_a_deep_letterhead():
    """A 6-line, 128pt-deep letterhead (clinic name, patient name, MRN, DOB,
    address, phone) must land entirely in the header. The old implementation
    hard-capped the search at 15% of the page (≈118.8pt on a standard
    792pt-tall page), which put MRN/DOB/address/phone into body. There is no
    internal gap within this letterhead (each row's bbox touches the next),
    so the true boundary is the very first gap encountered on the page —
    this pins down the "no depth ceiling" fix rather than a disguised one."""
    width, height = 612.0, 792.0
    blocks = [
        block("Clinic Name", 40, 20, 300, 38),
        block("Patient Name", 40, 38, 300, 56),
        block("MRN: 123", 40, 56, 300, 74),
        block("DOB: 1/1/1990", 40, 74, 300, 92),
        block("Address line", 40, 92, 300, 110),
        block("Phone: 555-1234", 40, 110, 300, 128),
        block("Chief complaint: shoulder pain", 40, 170, 400, 185),
    ]
    header, sidebar, body = split_regions(blocks, width, height)
    assert {b.text for b in header} == {
        "Clinic Name", "Patient Name", "MRN: 123", "DOB: 1/1/1990",
        "Address line", "Phone: 555-1234",
    }
    assert {b.text for b in body} == {"Chief complaint: shoulder pain"}
    assert len(header) + len(sidebar) + len(body) == len(blocks)  # nothing dropped


def test_split_regions_finds_a_real_gutter_on_a_page_that_has_one():
    """Reaffirms real two-column pages (e.g. a medications rail) still get a
    non-empty sidebar under the finding-4 fix, and that the partition
    invariant holds."""
    width, height = 612.0, 792.0
    blocks = (
        [block("Clinic", 40, 20)]
        + [block(f"side {i}", 30, 200 + i * 20, 120, 210 + i * 20) for i in range(4)]
        + [block(f"body {i}", 260, 200 + i * 20, 560, 210 + i * 20) for i in range(4)]
    )
    header, sidebar, body = split_regions(blocks, width, height)
    assert len(sidebar) == 4 and len(body) == 4
    assert all(b.text.startswith("side") for b in sidebar)
    assert all(b.text.startswith("body") for b in body)
    assert len(header) + len(sidebar) + len(body) == len(blocks)


def test_split_regions_returns_empty_sidebar_when_no_gutter_exists():
    """A continuation page whose content spans the full width (no genuine
    two-column split, like a single-column Impression/Plan section) must not
    have a sidebar manufactured out of a fixed fallback fraction. Everything
    below the header lands in body instead."""
    width, height = 612.0, 792.0
    blocks = (
        [block("Clinic", 40, 20)]
        + [block(f"line {i}", 35, 200 + i * 20, 580, 210 + i * 20) for i in range(6)]
    )
    header, sidebar, body = split_regions(blocks, width, height)
    assert sidebar == []
    assert {b.text for b in body} == {f"line {i}" for i in range(6)}
    assert len(header) + len(sidebar) + len(body) == len(blocks)


def test_load_pages_has_no_phantom_sidebar_on_full_width_pages(sample_pdf_bytes):
    """On the provided chart, pages 1 and 4 have a genuine sidebar (a
    medications rail). Pages 2 (a two-column exam table), 3 (Impression/Plan,
    single column), and 5 have no genuine gutter and must not have a sidebar
    manufactured for them — everything below the header belongs in body."""
    pages = load_pages(sample_pdf_bytes)
    has_sidebar = {p.page: bool(p.sidebar) for p in pages}
    assert has_sidebar == {1: True, 2: False, 3: False, 4: True, 5: False}
    # Impression/Plan, previously stranded in the phantom sidebar, must now
    # be reachable in body.
    page3 = next(p for p in pages if p.page == 3)
    assert "Impression/Plan" in text_of(page3.body)
