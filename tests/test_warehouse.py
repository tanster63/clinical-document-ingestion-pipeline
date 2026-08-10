from datetime import date

import pytest

from ingestion.models import (
    Diagnosis, Document, Encounter, ExtractedDocument, MedicationSnapshot, Patient, Vitals,
)
from ingestion.warehouse import MERGE_KEYS, merge_sql, refresh_sql, rows_for


def make_doc() -> ExtractedDocument:
    return ExtractedDocument(
        document=Document(document_id="d1", gcs_uri="gs://b/chart.pdf",
                          file_name="chart.pdf", file_bytes=100, page_count=5,
                          ingest_run_id="r1"),
        patient=Patient(patient_id="4820917", mrn="4820917", family_name="BARLOW"),
        encounters=[Encounter(encounter_id="e1", patient_id="4820917",
                              encounter_date=date(2025, 7, 23))],
        vitals=[Vitals(encounter_id="e1", patient_id="4820917", height_in=67.0)],
        diagnoses=[Diagnosis(diagnosis_id="x1", encounter_id="e1", patient_id="4820917",
                             diagnosis_text="Pain in right shoulder",
                             icd10_code="M25.511")],
        medications=[MedicationSnapshot(encounter_id="e1", patient_id="4820917",
                                        medication_name="nebivolol")],
    )


def test_rows_for_produces_json_safe_rows_per_table():
    rows = rows_for(make_doc())
    assert rows["encounters"][0]["encounter_date"] == "2025-07-23"
    assert rows["patients"][0]["mrn"] == "4820917"
    assert rows["diagnoses"][0]["icd10_code"] == "M25.511"
    assert rows["vitals"][0]["height_in"] == 67.0


def test_empty_tables_are_omitted_not_emitted_empty():
    rows = rows_for(make_doc())
    assert "prescriptions" not in rows
    assert "imaging_studies" not in rows
    assert "exam_findings" not in rows


def test_every_emitted_table_has_a_declared_merge_key():
    for table in rows_for(make_doc()):
        assert table in MERGE_KEYS, f"{table} has no natural key declared"


def test_every_merge_key_column_is_present_on_the_rows_it_keys():
    rows = rows_for(make_doc())
    for table, table_rows in rows.items():
        for key in MERGE_KEYS[table]:
            assert all(key in row for row in table_rows), f"{table}.{key}"


def test_merge_sql_matches_on_the_natural_key_only(cfg):
    sql = merge_sql(cfg, "encounters", "_stg_encounters_abc",
                    ["encounter_id", "patient_id", "encounter_date", "provider_name"])
    assert "MERGE" in sql
    assert "WHEN MATCHED THEN UPDATE SET" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql
    assert "T.encounter_id = S.encounter_id" in sql.split("WHEN")[0]


def test_merge_sql_never_updates_the_key_column(cfg):
    sql = merge_sql(cfg, "encounters", "_stg_x", ["encounter_id", "provider_name"])
    update_clause = sql.split("WHEN MATCHED THEN UPDATE SET")[1].split("WHEN NOT MATCHED")[0]
    assert "encounter_id" not in update_clause
    assert "provider_name = S.provider_name" in update_clause


def test_merge_sql_handles_a_composite_key(cfg):
    """The medication snapshot has no surrogate id: its identity is the pair
    (encounter, drug), which is exactly its grain."""
    sql = merge_sql(cfg, "medication_snapshots", "_stg_m",
                    ["encounter_id", "medication_name", "route"])
    on_clause = sql.split("ON ")[1].split("WHEN")[0]
    assert "T.encounter_id = S.encounter_id" in on_clause
    assert "T.medication_name = S.medication_name" in on_clause
    assert "route = S.route" in sql.split("UPDATE SET")[1]


def test_a_table_whose_columns_are_all_key_emits_no_update_branch(cfg):
    sql = merge_sql(cfg, "medication_snapshots", "_stg_m",
                    ["encounter_id", "medication_name"])
    assert "WHEN MATCHED" not in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql


def test_merge_sql_is_fully_qualified(cfg):
    sql = merge_sql(cfg, "patients", "_stg_p", ["patient_id", "mrn"])
    assert f"`{cfg.project_id}.{cfg.dataset}.patients`" in sql
    assert f"`{cfg.project_id}.{cfg.dataset}._stg_p`" in sql


def test_the_declared_merge_keys_are_the_ones_the_design_argues_for():
    assert MERGE_KEYS["encounters"] == ("encounter_id",)
    assert MERGE_KEYS["vitals"] == ("encounter_id",)
    assert MERGE_KEYS["diagnoses"] == ("diagnosis_id",)
    assert MERGE_KEYS["medication_snapshots"] == ("encounter_id", "medication_name")


@pytest.mark.parametrize("fragment", [
    "encounter_seq", "ROW_NUMBER()", "drug_class", "ref_drug_class",
    "first_seen_date",
])
def test_derived_columns_are_recomputed_warehouse_wide_after_a_load(cfg, fragment):
    """A patient's visit ordinal and a drug's class span every document, so
    neither can be trusted from whichever export arrived last."""
    assert any(fragment in statement for statement in refresh_sql(cfg))


def test_refresh_statements_are_scoped_to_the_configured_dataset(cfg):
    for statement in refresh_sql(cfg):
        assert f"{cfg.project_id}.{cfg.dataset}." in statement
        assert "${" not in statement
