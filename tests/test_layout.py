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
