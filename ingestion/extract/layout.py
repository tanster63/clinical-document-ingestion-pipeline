"""Turn a PDF page into three ordered regions: header band, sidebar, body.

PyMuPDF hands back text in an arbitrary order, so nothing downstream may read
raw block order. Both boundaries are computed from the page's own whitespace,
which keeps the parser working if a chart is rendered at a different page size.
"""

import re
from dataclasses import dataclass

import fitz

PAGE_LABEL_RE = re.compile(r"\bPage\s+(\d+)\b", re.IGNORECASE)

# Fraction of the page used only as a fallback when whitespace analysis finds
# no gap at all anywhere on the page — never as a depth ceiling on the primary
# search, which scans the full page height.
FALLBACK_HEADER_FRACTION = 0.18
GUTTER_SEARCH_MIN = 0.10
GUTTER_SEARCH_MAX = 0.45
ROW_TOLERANCE = 3.0  # points; lines within this vertical distance are one row
# A candidate header/body gap below this size is treated as intra-header
# jitter (e.g. title-to-subtitle spacing), never as the boundary itself.
HEADER_GAP_NOISE_FLOOR = ROW_TOLERANCE
# A single content run at least this tall already reads as a genuine band on
# its own (e.g. a merged, tightly-packed identity block with no internal
# gaps), so a gap right after it can be trusted even before a second,
# separately-delimited row has been seen. This does not bound how deep the
# cut can land — it only gates how much content must accumulate before a
# gap is allowed to be trusted, so a taller header simply needs a taller
# qualifying run or a second row, never a fixed pixel ceiling.
MIN_STRUCTURAL_RUN_HEIGHT = ROW_TOLERANCE * 12
# When only a single short run precedes a candidate gap (too little evidence
# for the row-count or accumulated-height gates above), the gap is still
# trusted if it is at least this many multiples of that run's own height.
# Ordinary line-to-line leading for text of a given size is a fraction of
# that size, not multiples of it, so a gap this much larger than the one
# line above it cannot be routine leading — it is measured relative to the
# run's own height, so it scales with font size rather than being a fixed
# pixel amount.
SINGLE_ROW_GAP_MULTIPLE = 2.0
# An x-band still counts as a gutter while at most this fraction of the page's
# text rows cross it. A footer rule crosses the gutter on every page that has
# one; a column of text occupies a large share of the rows. Counting rows
# rather than testing "did anything touch this band" is what tells those two
# apart, and expressing the tolerance as a fraction keeps it independent of
# page size, font size, and how much text the page happens to carry.
GUTTER_MAX_ROW_FRACTION = 0.05
# A sidebar is a rail: materially narrower than the column beside it, both
# measured from the gutter outwards. Two columns of comparable width are a body
# table — the provided chart prints its right/left exam findings that way — and
# must stay in body together, or half of every exam lands in the medication rail.
SIDEBAR_MAX_WIDTH_RATIO = 0.5


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
    """Page, then row, then column.

    The page term matters as soon as blocks from more than one page are
    concatenated — an encounter spans several pages, and sorting those by y
    alone would interleave page 2's first line with page 1's last one,
    scrambling every section boundary downstream.
    """
    return sorted(blocks, key=lambda b: (b.page, round(b.y0 / ROW_TOLERANCE), b.x0))


@dataclass(frozen=True)
class Line:
    """One visual row: the blocks that share a baseline, joined left to right.

    PDF extraction splits a printed line wherever formatting changes, so
    "1." and "Shoulder Pain, Right" arrive as two blocks of the same row.
    Parsers that reason about lines need them back together.
    """

    text: str
    page: int
    y0: float
    x0: float
    blocks: list[Block]


def lines_of(blocks: list[Block]) -> list[Line]:
    grouped: dict[tuple[int, int], list[Block]] = {}
    for block in reading_order(blocks):
        grouped.setdefault((block.page, round(block.y0 / ROW_TOLERANCE)), []).append(block)
    lines: list[Line] = []
    for (page, _), row in grouped.items():
        text = " ".join(b.text for b in row).strip()
        lines.append(
            Line(text=text, page=page, y0=min(b.y0 for b in row),
                 x0=min(b.x0 for b in row), blocks=row)
        )
    return lines


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
    """Bottom of the repeating header band: the midpoint of the first empty
    whitespace gap that is structurally trustworthy as a section break.

    The search scans the *entire* page height — there is no depth ceiling,
    so a taller letterhead is handled by finding a later gap, not by being
    clipped. It bins the actual glyph extent (y0..y1) of every block, not
    just the baseline (y0) point: baseline-to-baseline distance is noisy,
    since a large title's tall bounding box can create a bigger apparent gap
    between two header lines than the real whitespace separating the header
    band from the body, whereas actual empty vertical space is not.

    A raw "widest gap anywhere on the page" search fails: an unrelated
    paragraph break deep in the body is often wider than the true, shallow
    header/body gap. A raw "first gap over some fixed size" search also
    fails: a two-line synthetic header can have an internal line-to-line gap
    of the same order of magnitude as a real chart's true header/body gap,
    so no single fixed size threshold can accept one and reject the other.

    Instead a candidate gap is only trusted once *some* structural content
    has accumulated above it — either a second, separately-delimited row
    (row_count >= 2), a single run already taller than
    MIN_STRUCTURAL_RUN_HEIGHT (covers a tightly-packed, gapless multi-line
    letterhead that never registers a second row), or — when there is only
    one short row and neither of those apply — a gap at least
    SINGLE_ROW_GAP_MULTIPLE times that row's own height (covers a genuine
    single-line header, e.g. just a clinic name, followed by real
    whitespace; the ratio keeps this relative to the row's own font size
    rather than a fixed pixel amount, so it does not fire on ordinary
    line-to-line leading in body text, which is a fraction of a line's
    height, not a multiple of it). Even when trustworthy, a gap is only
    accepted if it is larger than every gap rejected before it (a running
    "new local maximum" requirement, seeded at HEADER_GAP_NOISE_FLOOR so
    pure jitter can never seed the comparison). This lets the first small
    intra-header gap be skipped as noise while the very next, genuinely
    larger gap is accepted regardless of how deep on the page it falls.
    """
    bins = max(1, round(height))
    bin_h = height / bins
    occupied = [False] * bins
    for b in blocks:
        start = max(0, int(b.y0 / bin_h))
        end = min(bins - 1, int(min(b.y1, height) / bin_h))
        for i in range(start, end + 1):
            occupied[i] = True

    row_count = 0
    accumulated_height = 0.0
    last_run_height = 0.0
    max_prior_gap = 0.0
    run_start: int | None = None
    gap_start: int | None = None
    seen_content = False

    for i in range(bins):
        if occupied[i]:
            seen_content = True
            if gap_start is not None:
                gap_len = (i - gap_start) * bin_h
                trustworthy = (
                    row_count >= 2
                    or accumulated_height >= MIN_STRUCTURAL_RUN_HEIGHT
                    or (row_count == 1 and gap_len > SINGLE_ROW_GAP_MULTIPLE * last_run_height)
                )
                if trustworthy and gap_len > max(max_prior_gap, HEADER_GAP_NOISE_FLOOR):
                    return (gap_start + i) / 2 * bin_h
                max_prior_gap = max(max_prior_gap, gap_len)
                gap_start = None
            if run_start is None:
                run_start = i
            continue
        # Empty bin: close out any run that just ended.
        if run_start is not None:
            last_run_height = (i - run_start) * bin_h
            accumulated_height += last_run_height
            row_count += 1
            run_start = None
        if not seen_content:
            # Blank margin above the first block is not a header/body gap.
            continue
        if gap_start is None:
            gap_start = i
    return height * FALLBACK_HEADER_FRACTION


def gutter_band(blocks: list[Block], width: float) -> tuple[float, float] | None:
    """Sidebar/body boundary: the widest empty vertical band in the left
    portion of the page, returned as (left edge, right edge), or None if this
    page has no genuine gutter (its content spans the full width, e.g. a
    single-column continuation page or a two-column body table).

    The whole band is returned rather than a single cut line because a
    sidebar's right edge is ragged — one long drug name reaches much further
    right than the rest of the rail. A cut at the band's midpoint slices that
    longest line into the body; keeping the band lets a block be judged
    against the edge that matters for it.

    Returning None — rather than substituting a fixed fraction of the page
    width — is what lets split_regions tell "no sidebar on this page" apart
    from "found one." Manufacturing a split at a fixed fraction when no
    empty band exists would carve an arbitrary vertical slice out of
    full-width content and misclassify it as a sidebar.

    Emptiness is measured by how many text *rows* cross a band, not by
    whether anything touched it. A full-width footer line crosses the gutter
    on every page that has one, and a binary "anything touched it" test lets
    that single line erase a real two-column split — which is exactly what it
    did to the rendered corpus, dumping the medication rail into the body and
    scrambling every section boundary after it. A band stays a gutter while no
    more than GUTTER_MAX_ROW_FRACTION of the page's rows reach into it, so a
    footer rule is tolerated and a column of text never is.
    """
    if not blocks:
        return None
    bins = 200
    bin_width = width / bins

    rows_in_bin: list[set[int]] = [set() for _ in range(bins)]
    all_rows: set[int] = set()
    for b in blocks:
        row = round(b.y0 / ROW_TOLERANCE)
        all_rows.add(row)
        start = max(0, int(b.x0 / bin_width))
        end = min(bins - 1, int(b.x1 / bin_width))
        for i in range(start, end + 1):
            rows_in_bin[i].add(row)
    if not all_rows:
        return None
    tolerance = GUTTER_MAX_ROW_FRACTION * len(all_rows)
    free = [len(rows) <= tolerance for rows in rows_in_bin]

    lo, hi = int(bins * GUTTER_SEARCH_MIN), int(bins * GUTTER_SEARCH_MAX)
    best_len, best_run, run_start = 0, None, None
    for i in range(lo, hi + 1):
        if free[i]:
            run_start = i if run_start is None else run_start
            length = i - run_start + 1
            if length > best_len:
                best_len, best_run = length, (run_start, i)
        else:
            run_start = None
    if best_run is None or best_len < 2:
        return None
    return best_run[0] * bin_width, (best_run[1] + 1) * bin_width


def split_regions(
    blocks: list[Block], width: float, height: float
) -> tuple[list[Block], list[Block], list[Block]]:
    """Partition blocks into (header, sidebar, body). Every block lands in
    exactly one region — nothing is discarded. Pages with no genuine gutter
    (content spans the full width) get an empty sidebar, with everything
    below the header landing in body.

    A block that crosses the whole gutter belongs to neither column — it is a
    full-width element such as a footer rule — and stays in body.
    """
    cut = header_cut_y(blocks, height)
    header = [b for b in blocks if b.y0 < cut]
    below = [b for b in blocks if b.y0 >= cut]
    band = gutter_band(below, width) if below else None
    sidebar: list[Block] = []
    body = list(below)
    if band is not None:
        band_start, band_end = band
        left = [b for b in below if b.x1 <= band_end]
        right = [b for b in below if b.x1 > band_end]
        if left and right:
            # The rail's own width, against everything to the right of the
            # gutter. The right side is measured from the gutter rather than
            # from its leftmost block, because a full-width footer would
            # otherwise stretch it and make any two columns look like a rail.
            left_width = max(b.x1 for b in left) - min(b.x0 for b in left)
            right_width = max(b.x1 for b in right) - band_end
            if left_width <= SIDEBAR_MAX_WIDTH_RATIO * right_width:
                sidebar, body = left, right
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
