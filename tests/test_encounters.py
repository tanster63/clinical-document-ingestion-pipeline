from datetime import date

from ingestion.extract.encounters import find_dates, service_date_of, split_encounters
from ingestion.extract.layout import Block, PageLayout, load_pages


def page(number, header_text, label, body_text="body"):
    def blk(text, x0, y0):
        return Block(text=text, x0=x0, y0=y0, x1=x0 + 100, y1=y0 + 10, page=number)
    return PageLayout(
        page=number, width=612.0, height=792.0,
        header=[blk(header_text, 40, 20)],
        sidebar=[blk("meds", 30, 200)],
        body=[blk(body_text, 260, 200)],
        page_label=label,
    )


def test_find_dates_parses_us_format_and_ignores_junk():
    assert find_dates("DOS: 07/23/2025 and 8/13/2025") == [date(2025, 7, 23), date(2025, 8, 13)]
    assert find_dates("no dates 13/45/2025") == []


def test_find_dates_reads_the_long_form_the_provided_chart_prints():
    """The provided export heads each visit "Visit Note - July 23, 2025"."""
    assert find_dates("Visit Note - July 23, 2025") == [date(2025, 7, 23)]
    assert find_dates("Aug 5, 2025 and 12/01/2024") == [date(2025, 8, 5), date(2024, 12, 1)]


def test_page_counter_reset_starts_a_new_encounter():
    pages = [
        page(1, "Date of Service: 07/23/2025", 1),
        page(2, "Date of Service: 07/23/2025", 2),
        page(3, "Date of Service: 07/23/2025", 3),
        page(4, "Date of Service: 08/13/2025", 1),
        page(5, "Date of Service: 08/13/2025", 2),
    ]
    result = split_encounters(pages)
    assert [(e.page_start, e.page_end) for e in result] == [(1, 3), (4, 5)]
    assert [e.encounter_date for e in result] == [date(2025, 7, 23), date(2025, 8, 13)]


def test_changed_service_date_splits_even_without_a_counter_reset():
    pages = [
        page(1, "Date of Service: 05/14/2025", 1),
        page(2, "Date of Service: 06/25/2025", 2),
    ]
    assert [(e.page_start, e.page_end) for e in split_encounters(pages)] == [(1, 1), (2, 2)]


def test_single_encounter_document_stays_whole():
    pages = [page(n, "Date of Service: 07/02/2025", n) for n in (1, 2)]
    result = split_encounters(pages)
    assert len(result) == 1
    assert (result[0].page_start, result[0].page_end) == (1, 2)


def test_date_of_birth_is_never_mistaken_for_the_service_date():
    pages = [page(1, "DOB: 09/15/1991 Date of Service: 07/23/2025", 1)]
    assert split_encounters(pages, date_of_birth=date(1991, 9, 15))[0].encounter_date == \
        date(2025, 7, 23)


def test_an_unlabelled_date_of_birth_is_still_excluded():
    """The provided chart splits its DOB label from its value across two rows,
    so the label regex cannot see them together — the caller's known DOB has to
    be enough on its own."""
    pages = [page(1, "DOB: Sex: MRN: 09/15/1991 Male 4820917 Visit Note - July 23, 2025", 1)]
    assert split_encounters(pages, date_of_birth=date(1991, 9, 15))[0].encounter_date == \
        date(2025, 7, 23)


def test_service_date_of_prefers_the_labelled_date_over_any_earlier_one():
    assert service_date_of(
        page(1, "Printed 01/02/2025 Date of Service: 07/23/2025", 1), None
    ) == date(2025, 7, 23)


def test_document_with_no_dates_still_yields_one_encounter():
    result = split_encounters([page(1, "no dates at all", None)])
    assert len(result) == 1 and result[0].encounter_date is None


def test_provided_chart_splits_into_two_encounters(sample_pdf_bytes):
    result = split_encounters(load_pages(sample_pdf_bytes), date_of_birth=date(1991, 9, 15))
    assert len(result) == 2
    assert [e.encounter_date for e in result] == [date(2025, 7, 23), date(2025, 8, 13)]
    assert (result[0].page_start, result[0].page_end) == (1, 3)
    assert (result[1].page_start, result[1].page_end) == (4, 5)
