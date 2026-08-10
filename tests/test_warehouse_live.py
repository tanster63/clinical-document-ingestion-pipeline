"""Idempotency against real BigQuery.

This is the single most important assertion in the project, and it cannot be
faked: MERGE semantics, the streaming-buffer caveat and load-job visibility are
all properties of the service, not of this code. It is skipped without
credentials so the fast loop stays fast, and it is run before the demo.

    set -a; source .env; set +a
    RUN_LIVE_TESTS=1 pytest tests/test_warehouse_live.py -v
"""

import os

import pytest

from ingestion.config import load_config
from ingestion.extract.pipeline import extract_document
from ingestion.warehouse import Warehouse

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="live BigQuery test; set RUN_LIVE_TESTS=1 with a sourced .env to run",
)

COUNTED = ("patients", "encounters", "vitals", "diagnoses", "prescriptions",
           "medication_snapshots", "imaging_studies", "exam_findings", "documents")


@pytest.fixture(scope="module")
def live():
    from google.cloud import bigquery
    cfg = load_config()
    return cfg, bigquery.Client(project=cfg.project_id)


def counts(cfg, client) -> dict[str, int]:
    union = " UNION ALL ".join(
        f"SELECT '{t}' AS t, COUNT(*) AS n FROM `{cfg.table(t)}`" for t in COUNTED
    )
    return {row["t"]: row["n"] for row in client.query(union).result()}


def test_ingesting_the_same_chart_twice_changes_no_row_count(live, sample_pdf_bytes):
    cfg, client = live
    warehouse = Warehouse(cfg, client=client)
    doc = extract_document(sample_pdf_bytes, file_name="golden-idempotency.pdf", cfg=cfg)

    warehouse.write_document(doc)
    after_first = counts(cfg, client)
    warehouse.write_document(doc)
    assert counts(cfg, client) == after_first


def test_a_re_export_that_overlaps_does_not_duplicate_the_encounter(live, sample_pdf_bytes):
    """Same visits, different file name: encounter grain must not double."""
    cfg, client = live
    warehouse = Warehouse(cfg, client=client)
    warehouse.write_document(
        extract_document(sample_pdf_bytes, file_name="export-a.pdf", cfg=cfg))
    before = counts(cfg, client)["encounters"]
    warehouse.write_document(
        extract_document(sample_pdf_bytes, file_name="export-b.pdf", cfg=cfg))
    assert counts(cfg, client)["encounters"] == before


def test_derived_columns_are_correct_after_a_merge(live):
    cfg, client = live
    rows = list(client.query(f"""
        SELECT patient_id, encounter_date, encounter_seq,
               ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY encounter_date,
                                  encounter_id) AS expected
        FROM `{cfg.table('encounters')}`
    """).result())
    assert rows, "no encounters in the warehouse to check"
    assert all(row["encounter_seq"] == row["expected"] for row in rows)


def test_the_views_answer_the_brief_s_cross_table_question(live):
    cfg, client = live
    rows = list(client.query(f"""
        SELECT mrn, encounter_date FROM `{cfg.table('v_encounter_summary')}`
        WHERE anti_inflammatory_prescribed AND imaging_same_day
    """).result())
    assert rows, "the corpus was built so this question has a non-empty answer"


def test_no_staging_tables_are_left_behind(live):
    cfg, client = live
    leftover = [t.table_id for t in client.list_tables(cfg.dataset_ref)
                if t.table_id.startswith("_stg_")]
    assert leftover == []
