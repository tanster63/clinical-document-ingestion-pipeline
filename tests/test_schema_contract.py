"""The DDL, the Pydantic models and the schema document must agree.

Nothing else enforces this. A load job hands BigQuery a JSON row and lets it
match column names, so a model field that drifts from its column does not raise
— it silently lands NULL, or fails one deploy later with an error that points at
the wrong place. These tests read `sql/ddl/schema.sql` directly, so it stays the
single definition of the warehouse.
"""

import re
from pathlib import Path

import pytest

from ingestion import models
from ingestion.warehouse import MERGE_KEYS

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = REPO_ROOT / "sql/ddl/schema.sql"
SCHEMA_DOC = REPO_ROOT / "docs/schema.md"
VIEWS_SQL = REPO_ROOT / "sql/ddl/views.sql"

TABLE_RE = re.compile(
    r"CREATE TABLE IF NOT EXISTS `\$\{PROJECT\}\.\$\{DATASET\}\.(\w+)`\s*\((.*?)\n\)",
    re.S,
)
COLUMN_RE = re.compile(r"^\s*(\w+)\s+(STRING|INT64|FLOAT64|BOOL|DATE|TIMESTAMP)\b")

MODEL_FOR_TABLE = {
    "documents": models.Document,
    "patients": models.Patient,
    "encounters": models.Encounter,
    "vitals": models.Vitals,
    "diagnoses": models.Diagnosis,
    "prescriptions": models.Prescription,
    "medication_snapshots": models.MedicationSnapshot,
    "patient_history": models.PatientHistory,
    "procedures": models.Procedure,
    "imaging_studies": models.ImagingStudy,
    "exam_findings": models.ExamFinding,
    "ingestion_issues": models.IngestionIssue,
    "ingest_runs": models.IngestRun,
}


def ddl_tables() -> dict[str, list[str]]:
    tables = {}
    for name, body in TABLE_RE.findall(SCHEMA_SQL.read_text()):
        columns = [m.group(1) for line in body.splitlines() if (m := COLUMN_RE.match(line))]
        tables[name] = columns
    return tables


TABLES = ddl_tables()


def test_the_ddl_declares_the_tables_the_design_calls_for():
    assert set(TABLES) == set(MODEL_FOR_TABLE) | {"ref_drug_class"}


@pytest.mark.parametrize("table", sorted(MODEL_FOR_TABLE))
def test_every_model_matches_its_table_column_for_column(table):
    """A drifted field name loads as NULL rather than failing, so nothing else
    would catch it until a query came back empty."""
    fields = set(MODEL_FOR_TABLE[table].model_fields)
    columns = set(TABLES[table])
    assert fields - columns == set(), f"{table}: model has fields the table lacks"
    assert columns - fields == set(), f"{table}: table has columns the model never fills"


@pytest.mark.parametrize("table", sorted(MERGE_KEYS))
def test_every_merge_key_is_a_real_column_of_a_real_table(table):
    assert table in TABLES, f"{table} is merged into but not declared"
    for key in MERGE_KEYS[table]:
        assert key in TABLES[table], f"{table}.{key} is a merge key but not a column"


def test_every_table_has_a_declared_merge_key_or_is_seed_data():
    unkeyed = set(TABLES) - set(MERGE_KEYS) - {"ref_drug_class"}
    assert unkeyed == set(), f"no natural key declared for {sorted(unkeyed)}"


def test_the_schema_document_covers_every_table_and_column():
    doc = SCHEMA_DOC.read_text()
    for table, columns in TABLES.items():
        assert f"`{table}`" in doc, f"docs/schema.md does not document {table}"
        for column in columns:
            assert f"`{column}`" in doc, f"docs/schema.md does not document {table}.{column}"


def test_only_the_four_documented_columns_are_llm_derived():
    """The boundary is stated as a rule, so it has to be checkable as one."""
    llm_columns = {"body_region", "laterality", "visit_type", "hpi_summary"}
    assert llm_columns <= set(TABLES["encounters"])
    for table, columns in TABLES.items():
        if table == "encounters":
            continue
        assert "hpi_summary" not in columns
        assert "llm_confidence" not in columns


def test_the_views_only_read_tables_that_exist():
    referenced = set(re.findall(r"\$\{PROJECT\}\.\$\{DATASET\}\.(\w+)`", VIEWS_SQL.read_text()))
    views = {"v_encounter_summary", "v_patient_timeline"}
    assert referenced - views <= set(TABLES)


def test_the_agent_only_reads_views_that_exist():
    from agent.tools import ALLOWED_VIEWS
    declared = set(re.findall(r"CREATE OR REPLACE VIEW `\$\{PROJECT\}\.\$\{DATASET\}\.(\w+)`",
                              VIEWS_SQL.read_text()))
    assert set(ALLOWED_VIEWS) == declared


SHIPPED = ("ingestion", "agent", "corpus", "sql", "scripts")


def test_no_deployment_literal_is_committed_in_shipped_source():
    """Project ID, dataset, bucket, region and model name live in .env and are
    read through config.py. Anywhere else is a value that will go stale."""
    from ingestion.config import DEFAULT_GEMINI_MODEL, DEFAULT_LOCATION

    offenders = []
    for directory in SHIPPED:
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if path.suffix not in {".py", ".sql", ".sh"} or path.name == "config.py":
                continue
            text = path.read_text()
            for literal in (DEFAULT_GEMINI_MODEL, DEFAULT_LOCATION):
                if literal in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} names {literal!r}")
    assert offenders == [], offenders
