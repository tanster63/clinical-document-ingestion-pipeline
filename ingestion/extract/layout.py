"""Turn a PDF page into three ordered regions: header band, sidebar, body.

PyMuPDF hands back text in an arbitrary order, so nothing downstream may read
raw block order. Both boundaries are computed from the page's own whitespace,
which keeps the parser working if a chart is rendered at a different page size.
"""

import re
from dataclasses import dataclass

import fitz

PAGE_LABEL_RE = re.compile(r"\bPage\s+(\d+)\b", re.IGNORECASE)

# Fractions of the page used only as fallbacks when whitespace analysis finds
# nothing — never as the primary boundary.
FALLBACK_HEADER_FRACTION = 0.18
FALLBACK_GUTTER_FRACTION = 0.28
GUTTER_SEARCH_MIN = 0.10
GUTTER_SEARCH_MAX = 0.45
# The repeating identity/branding band never runs deeper than this fraction of
# the page. Keeping the search window tight is what makes the *widest* empty
# band within it reliably be the header/body boundary rather than some
# unrelated paragraph break further down the page competing for "widest".
HEADER_SEARCH_MAX = 0.15
ROW_TOLERANCE = 3.0  # points; lines within this vertical distance are one row


@dataclass(frozen=True)
class Block:
    """One text line with its bounding box, in PDF points."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int


@dataclass(frozen=True)
class PageLayout:
    page: int
    width: float
    height: float
    header: list[Block]
    sidebar: list[Block]
    body: list[Block]
    page_label: int | None


def reading_order(blocks: list[Block]) -> list[Block]:
    return sorted(blocks, key=lambda b: (round(b.y0 / ROW_TOLERANCE), b.x0))


def text_of(blocks: list[Block]) -> str:
    return " ".join(b.text for b in reading_order(blocks)).strip()


def page_label_of(blocks: list[Block]) -> int | None:
    for b in blocks:
        match = PAGE_LABEL_RE.search(b.text)
        if match:
            return int(match.group(1))
    return None


def _blocks_of_page(page: "fitz.Page", page_number: int) -> list[Block]:
    out: list[Block] = []
    for raw in page.get_text("dict")["blocks"]:
        if raw.get("type") != 0:  # skip images
            continue
        for line in raw["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            x0, y0, x1, y1 = line["bbox"]
            out.append(Block(text=text, x0=x0, y0=y0, x1=x1, y1=y1, page=page_number))
    return out


def header_cut_y(blocks: list[Block], height: float) -> float:
    """Bottom of the repeating header band: the midpoint of the widest empty
    whitespace band in the top portion of the page.

    Mirrors gutter_x's whitespace-band search, but on the vertical axis and
    scoped to a tight top-of-page window (HEADER_SEARCH_MAX). It bins the
    *actual* glyph extent (y0..y1) of every block, not just the baseline
    (y0) point: baseline-to-baseline distance is noisy, since a large
    title's tall bounding box can create a bigger apparent gap between two
    header lines than the real whitespace separating the header band from
    the body, whereas actual empty vertical space is not. Restricting the
    search window keeps an unrelated paragraph break deeper in the body from
    ever competing with the true header/body gap for "widest".
    """
    limit = height * HEADER_SEARCH_MAX
    bins = 200
    bin_h = limit / bins
    occupied = [False] * bins
    for b in blocks:
        if b.y0 >= limit:
            continue
        start = max(0, int(b.y0 / bin_h))
        end = min(bins - 1, int(min(b.y1, limit) / bin_h))
        for i in range(start, end + 1):
            occupied[i] = True

    seen_content = False
    best_len, best_mid, run_start = 0, None, None
    for i in range(bins):
        if occupied[i]:
            seen_content = True
            run_start = None
            continue
        if not seen_content:
            # Blank margin above the first block is not a header/body gap.
            continue
        run_start = i if run_start is None else run_start
        length = i - run_start + 1
        if length > best_len:
            best_len, best_mid = length, (run_start + i) / 2
    if best_mid is None:
        return height * FALLBACK_HEADER_FRACTION
    return best_mid * bin_h


def gutter_x(blocks: list[Block], width: float) -> float:
    """Sidebar/body boundary: the midpoint of the widest empty vertical band in
    the left portion of the page."""
    bins = 200
    bin_width = width / bins
    occupied = [False] * bins
    for b in blocks:
        start = max(0, int(b.x0 / bin_width))
        end = min(bins - 1, int(b.x1 / bin_width))
        for i in range(start, end + 1):
            occupied[i] = True

    lo, hi = int(bins * GUTTER_SEARCH_MIN), int(bins * GUTTER_SEARCH_MAX)
    best_len, best_mid, run_start = 0, None, None
    for i in range(lo, hi + 1):
        if not occupied[i]:
            run_start = i if run_start is None else run_start
            length = i - run_start + 1
            if length > best_len:
                best_len, best_mid = length, (run_start + i) / 2
        else:
            run_start = None
    if best_mid is None or best_len < 2:
        return width * FALLBACK_GUTTER_FRACTION
    return best_mid * bin_width


def split_regions(
    blocks: list[Block], width: float, height: float
) -> tuple[list[Block], list[Block], list[Block]]:
    """Partition blocks into (header, sidebar, body). Every block lands in
    exactly one region — nothing is discarded."""
    cut = header_cut_y(blocks, height)
    header = [b for b in blocks if b.y0 < cut]
    below = [b for b in blocks if b.y0 >= cut]
    gutter = gutter_x(below, width) if below else width * FALLBACK_GUTTER_FRACTION
    sidebar = [b for b in below if b.x1 <= gutter]
    body = [b for b in below if b.x1 > gutter]
    return reading_order(header), reading_order(sidebar), reading_order(body)


def load_pages(pdf_bytes: bytes) -> list[PageLayout]:
    pages: list[PageLayout] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for index, page in enumerate(doc, start=1):
            blocks = _blocks_of_page(page, index)
            header, sidebar, body = split_regions(blocks, page.rect.width, page.rect.height)
            pages.append(
                PageLayout(
                    page=index,
                    width=page.rect.width,
                    height=page.rect.height,
                    header=header,
                    sidebar=sidebar,
                    body=body,
                    page_label=page_label_of(blocks),
                )
            )
    return pages
