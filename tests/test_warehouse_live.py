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


# --- the views have to be queryable, not merely creatable --------------------

def test_every_documented_view_survives_select_star(live):
    """`CREATE VIEW` validates a view's SQL but never runs it.

    v_patient_timeline was created, described and committed while being
    unreadable: its nested arrays were correlated subqueries over other tables,
    and BigQuery refuses those when it cannot de-correlate them. Every attempt
    to select one -- including a bare `SELECT *` -- came back 400. Nothing
    caught it until the deployed agent asked for a patient's history and got an
    HTTP 500, because no test had ever read from a view.
    """
    from agent.tools import ALLOWED_VIEWS

    cfg, client = live
    for view in ALLOWED_VIEWS:
        rows = list(client.query(f"SELECT * FROM `{cfg.table(view)}` LIMIT 5").result())
        assert rows, f"{view} returned no rows"


def test_the_timeline_view_unnests_its_arrays(live):
    """SELECT * can pass while UNNEST still fails -- UNNEST is what every
    question about prescriptions or history actually compiles down to."""
    cfg, client = live
    timeline = cfg.table("v_patient_timeline")

    hypertensive = next(iter(client.query(
        f"SELECT COUNT(DISTINCT t.mrn) AS n FROM `{timeline}` t, "
        "UNNEST(t.patient_history) h WHERE LOWER(h.item_text) LIKE '%hypertens%'"
    ).result()))["n"]
    assert hypertensive == 3, "left-rail history is patient-level; expected 3"

    written = [row["drug_name"] for row in client.query(
        f"SELECT rx.drug_name FROM `{timeline}` t, UNNEST(t.prescriptions_written) rx "
        "WHERE t.mrn = '4820917' AND t.encounter_date = '2025-07-23'"
    ).result()]
    assert written == ["meloxicam"]


def test_an_encounter_with_no_rows_in_a_child_table_gets_an_empty_array(live):
    """The correlated form returned [] for a childless encounter; a LEFT JOIN
    returns NULL. Callers distinguish the two, so the view coalesces."""
    cfg, client = live
    row = next(iter(client.query(
        f"SELECT COUNTIF(procedures IS NULL) AS null_count, "
        f"COUNTIF(ARRAY_LENGTH(procedures) = 0) AS empty_count "
        f"FROM `{cfg.table('v_patient_timeline')}`"
    ).result()))
    assert row["null_count"] == 0
    assert row["empty_count"] > 0, "expected some encounters with no procedures"


def test_run_sql_resolves_a_bare_view_name(live):
    """The model writes `FROM v_encounter_summary`, because that is the name
    get_schema hands it. Without a default dataset BigQuery rejected every such
    query, so each aggregate question burned a failed round trip before the
    agent retried with the dataset spelled out."""
    from agent.tools import run_sql
    result = run_sql("SELECT COUNT(*) AS n FROM v_encounter_summary")
    assert result["status"] == "ok", result.get("reason")
    assert result["rows"][0]["n"] > 0


def test_grouping_conditions_on_free_text_splits_a_real_condition(live):
    """Guards the rule in INSTRUCTION with the case that broke it: M77.11 is
    worded two ways across its two encounters, so a GROUP BY that carries the
    description reports it as two one-visit conditions instead of one seen
    twice -- which is what the agent did before the rule spelled this out."""
    cfg, client = live
    summary = cfg.table("v_encounter_summary")

    by_code = {row["primary_icd10_code"]: row["n"] for row in client.query(
        f"SELECT primary_icd10_code, COUNT(*) AS n FROM `{summary}` "
        "WHERE primary_icd10_code IS NOT NULL GROUP BY 1"
    ).result()}
    assert by_code["M77.11"] == 2

    split = [row["n"] for row in client.query(
        f"SELECT COUNT(*) AS n FROM `{summary}` WHERE primary_icd10_code = 'M77.11' "
        "GROUP BY primary_icd10_code, primary_diagnosis"
    ).result()]
    assert split == [1, 1], "the free-text split is the failure this rule prevents"
