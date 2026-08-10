"""BigQuery writer.

Idempotency (§4.3) lives here. Rows land in a per-run staging table via a
**load job**, then MERGE into the target on the natural key.

Two details are load-bearing and easy to get wrong:

* It has to be a load job, not a streaming insert. Rows sitting in BigQuery's
  streaming buffer are not reliably visible to MERGE, so the same chart ingested
  twice would duplicate — the failure would appear only under load, and only
  sometimes, which is the worst way to find it.
* It has to merge on the natural key, not delete-by-document-then-insert. The
  provided chart proves one PDF can carry several encounters, so a re-export
  that overlaps a previous document would duplicate at encounter grain. Merging
  on clinical identity is correct however the documents happen to slice up.

Two derived columns are recomputed after every merge rather than trusted from
whichever document arrived last: `encounter_seq` is a patient-wide ordinal that
no single document can know, and `drug_class` belongs to the drug, not to the
prescription that mentioned it.
"""

import uuid
from dataclasses import dataclass, field

from google.cloud import bigquery

from ingestion.config import Config
from ingestion.models import ExtractedDocument, IngestRun

MERGE_KEYS: dict[str, tuple[str, ...]] = {
    "documents": ("document_id",),
    "patients": ("patient_id",),
    "encounters": ("encounter_id",),
    "vitals": ("encounter_id",),
    "diagnoses": ("diagnosis_id",),
    "prescriptions": ("prescription_id",),
    "medication_snapshots": ("encounter_id", "medication_name"),
    "imaging_studies": ("imaging_id",),
    "exam_findings": ("finding_id",),
    "ingestion_issues": ("issue_id",),
    "ingest_runs": ("run_id",),
}

STAGING_TTL_HOURS = 6

REFRESH_STATEMENTS = (
    # A patient's visit ordinal is warehouse-wide, so it cannot be trusted from
    # whichever export happened to arrive last.
    """
    UPDATE `{encounters}` e
    SET encounter_seq = ranked.seq
    FROM (
      SELECT encounter_id,
             ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY encounter_date,
                                encounter_id) AS seq
      FROM `{encounters}`
    ) ranked
    WHERE ranked.encounter_id = e.encounter_id
      AND (e.encounter_seq IS NULL OR e.encounter_seq != ranked.seq)
    """,
    # Drug class is a property of the drug. Resolving it from the seed table
    # rather than from the model is what makes "on an anti-inflammatory"
    # answerable without anything being able to hallucinate a class (§4.3).
    """
    UPDATE `{prescriptions}` rx
    SET drug_class = rdc.drug_class
    FROM `{ref_drug_class}` rdc
    WHERE LOWER(rdc.drug_name) = LOWER(rx.drug_name)
      AND (rx.drug_class IS NULL OR rx.drug_class != rdc.drug_class)
    """,
    # first/last seen span every document, for the same reason as encounter_seq.
    """
    UPDATE `{patients}` p
    SET first_seen_date = span.first_seen, last_seen_date = span.last_seen
    FROM (
      SELECT patient_id, MIN(encounter_date) AS first_seen,
             MAX(encounter_date) AS last_seen
      FROM `{encounters}` GROUP BY patient_id
    ) span
    WHERE span.patient_id = p.patient_id
      AND (p.first_seen_date IS DISTINCT FROM span.first_seen
           OR p.last_seen_date IS DISTINCT FROM span.last_seen)
    """,
)


def rows_for(doc: ExtractedDocument) -> dict[str, list[dict]]:
    """Table name -> JSON-safe rows. Tables with nothing to write are omitted."""
    candidates = {
        "documents": [doc.document],
        "patients": [doc.patient],
        "encounters": doc.encounters,
        "vitals": doc.vitals,
        "diagnoses": doc.diagnoses,
        "prescriptions": doc.prescriptions,
        "medication_snapshots": doc.medications,
        "imaging_studies": doc.imaging,
        "exam_findings": doc.exam_findings,
        "ingestion_issues": doc.issues,
    }
    return {
        table: [row.to_row() for row in rows]
        for table, rows in candidates.items() if rows
    }


def merge_sql(cfg: Config, table: str, staging_table: str, columns: list[str]) -> str:
    keys = MERGE_KEYS[table]
    on_clause = " AND ".join(f"T.{key} = S.{key}" for key in keys)
    updatable = [column for column in columns if column not in keys]
    set_clause = ", ".join(f"{column} = S.{column}" for column in updatable)
    column_list = ", ".join(columns)
    values_list = ", ".join(f"S.{column}" for column in columns)

    update_branch = f"WHEN MATCHED THEN UPDATE SET {set_clause}\n" if updatable else ""
    return (
        f"MERGE `{cfg.table(table)}` T\n"
        f"USING `{cfg.table(staging_table)}` S\n"
        f"ON {on_clause}\n"
        f"{update_branch}"
        f"WHEN NOT MATCHED THEN INSERT ({column_list}) VALUES ({values_list})"
    )


def refresh_sql(cfg: Config) -> list[str]:
    """Statements that recompute warehouse-wide derived columns after a load."""
    names = {name: cfg.table(name)
             for name in ("encounters", "prescriptions", "patients", "ref_drug_class")}
    return [statement.format(**names).strip() for statement in REFRESH_STATEMENTS]


@dataclass
class Warehouse:
    cfg: Config
    client: "bigquery.Client | None" = None
    staging_ttl_hours: int = field(default=STAGING_TTL_HOURS)

    def __post_init__(self) -> None:
        self.client = self.client or bigquery.Client(project=self.cfg.project_id)

    def _load_staging(self, table: str, rows: list[dict]) -> str:
        """Load rows into a fresh staging table and return its name."""
        staging = f"_stg_{table}_{uuid.uuid4().hex[:12]}"
        target = self.client.get_table(self.cfg.table(table))
        job_config = bigquery.LoadJobConfig(
            schema=target.schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        self.client.load_table_from_json(
            rows, self.cfg.table(staging), job_config=job_config
        ).result()

        # Staging tables self-destruct so a failed run cannot litter the dataset.
        self.client.query(
            f"ALTER TABLE `{self.cfg.table(staging)}` "
            f"SET OPTIONS (expiration_timestamp = "
            f"TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {self.staging_ttl_hours} HOUR))"
        ).result()
        return staging

    def _merge(self, table: str, rows: list[dict]) -> int:
        staging = self._load_staging(table, rows)
        columns = sorted({column for row in rows for column in row})
        try:
            job = self.client.query(merge_sql(self.cfg, table, staging, columns))
            job.result()
            return job.num_dml_affected_rows or 0
        finally:
            self.client.delete_table(self.cfg.table(staging), not_found_ok=True)

    def write_document(self, doc: ExtractedDocument) -> dict[str, int]:
        """MERGE every non-empty table, then recompute derived columns.

        Returns table -> rows affected.
        """
        written: dict[str, int] = {}
        for table, rows in rows_for(doc).items():
            written[table] = self._merge(table, rows)
        self.refresh_derived()
        return written

    def refresh_derived(self) -> None:
        for statement in refresh_sql(self.cfg):
            self.client.query(statement).result()

    def record_run(self, run: IngestRun) -> None:
        self._merge("ingest_runs", [run.to_row()])
