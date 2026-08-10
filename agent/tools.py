"""Read-only tools exposed to the ADK agent.

The agent reads views, never base tables (§7). The views carry the joins and the
column names a clinical question actually uses, which keeps generated SQL simple
and leaves the physical schema free to change.

`run_sql` is the only place a model composes SQL against clinical data, so it is
guarded four ways before anything executes: statement-type allowlist, single
statement only, dataset and view scoping, and a dry-run byte cap.
"""

import re

from google.cloud import bigquery

try:
    from ingestion.config import load_config
except ModuleNotFoundError:  # pragma: no cover - deployed agent carries its own copy
    from agent._config import load_config

MAX_ROWS = 200
MAX_SCAN_BYTES = 200 * 1024 * 1024  # 200 MB
ALLOWED_VIEWS = ("v_encounter_summary", "v_patient_timeline")

FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|"
    r"REPLACE|CALL|EXPORT|LOAD|ASSERT|EXECUTE)\b",
    re.IGNORECASE,
)
STRING_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.IGNORECASE)
TABLE_REF_RE = re.compile(
    r"\bFROM\s+`?([A-Za-z0-9_.\-]+)`?|\bJOIN\s+`?([A-Za-z0-9_.\-]+)`?", re.IGNORECASE
)
# UNNEST(...) reads an array column of a view already cleared above, not a table.
UNNEST_RE = re.compile(r"\bUNNEST\s*\(", re.IGNORECASE)
# Names bound by a WITH clause are references to the query's own intermediate
# results, not to anything in the dataset.
CTE_RE = re.compile(r"(?:\bWITH\b|,)\s*([A-Za-z_]\w*)\s+AS\s*\(", re.IGNORECASE)


class SqlGuardError(ValueError):
    """The query was refused before it reached BigQuery."""


def _strip_literals(query: str) -> str:
    """Blank out string literals so punctuation inside them is not read as SQL."""
    return STRING_LITERAL_RE.sub("''", query)


def guard_sql(query: str, cfg) -> str:
    query = query.strip().rstrip(";").strip()
    if not query:
        raise SqlGuardError("empty query")

    skeleton = UNNEST_RE.sub("(", _strip_literals(query))

    if not re.match(r"^\s*(SELECT|WITH)\b", skeleton, re.IGNORECASE):
        raise SqlGuardError("only SELECT and WITH queries are allowed")
    if ";" in skeleton:
        raise SqlGuardError("only a single statement is allowed")
    forbidden = FORBIDDEN_RE.search(skeleton)
    if forbidden:
        raise SqlGuardError(
            f"{forbidden.group(1).upper()} is not permitted; this tool is read-only"
        )

    references = [ref for match in TABLE_REF_RE.finditer(skeleton)
                  for ref in match.groups() if ref]
    if not references:
        raise SqlGuardError("the query must read from one of the documented views")

    defined_here = {name.lower() for name in CTE_RE.findall(skeleton)}
    for reference in references:
        parts = reference.split(".")
        table = parts[-1]
        if len(parts) == 1 and table.lower() in defined_here:
            continue
        if len(parts) > 1 and parts[-2] != cfg.dataset:
            raise SqlGuardError(
                f"`{reference}` is outside dataset `{cfg.dataset}`; only that dataset is readable"
            )
        if table not in ALLOWED_VIEWS and not table.startswith("_"):
            raise SqlGuardError(
                f"`{table}` is not a readable view; use one of {', '.join(ALLOWED_VIEWS)}"
            )

    existing = LIMIT_RE.search(skeleton)
    if existing:
        if int(existing.group(1)) > MAX_ROWS:
            query = LIMIT_RE.sub(f"LIMIT {MAX_ROWS}", query)
    else:
        query = f"{query}\nLIMIT {MAX_ROWS}"
    return query


def _client_and_cfg():
    cfg = load_config()
    return bigquery.Client(project=cfg.project_id), cfg


def _rows(job) -> list[dict]:
    return [dict(row) for row in job.result()]


def get_schema() -> dict:
    """Describe the queryable views: their columns, types, and descriptions.

    Call this before composing any SQL, so column names are read rather than
    guessed.
    """
    client, cfg = _client_and_cfg()
    described = {}
    for view in ALLOWED_VIEWS:
        table = client.get_table(cfg.table(view))
        described[view] = {
            "description": table.description,
            "columns": [
                {"name": f.name, "type": f.field_type, "description": f.description}
                for f in table.schema
            ],
        }
    return {
        "dataset": cfg.dataset,
        "views": described,
        "note": "Only these views are readable. Queries are SELECT-only and row-limited. "
                "medications_on_arrival is what the patient was already taking as of that "
                "encounter; prescriptions_written is what was prescribed at it.",
    }


def find_patient(name_or_mrn: str) -> dict:
    """Look up patients by MRN, legal name, given or family name, or preferred name.

    Matching is case-insensitive and partial, so "Trey Barlow", "BARLOW", and
    "4820917" all resolve to the same patient.
    """
    client, cfg = _client_and_cfg()
    job = client.query(
        f"""
        SELECT DISTINCT mrn, family_name, given_name, preferred_name, legal_name,
               date_of_birth, sex, encounter_count, first_seen_date, last_seen_date
        FROM `{cfg.table('v_patient_timeline')}`
        WHERE LOWER(mrn) = LOWER(@term)
           OR LOWER(COALESCE(preferred_name, '')) LIKE CONCAT('%', LOWER(@term), '%')
           OR LOWER(COALESCE(legal_name, ''))     LIKE CONCAT('%', LOWER(@term), '%')
           OR LOWER(COALESCE(family_name, ''))    LIKE CONCAT('%', LOWER(@term), '%')
           OR LOWER(COALESCE(given_name, ''))     LIKE CONCAT('%', LOWER(@term), '%')
        ORDER BY family_name
        LIMIT 25
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("term", "STRING", name_or_mrn)
        ]),
    )
    matches = _rows(job)
    return {"query": name_or_mrn, "match_count": len(matches), "matches": matches}


def patient_timeline(patient_mrn: str) -> dict:
    """Every encounter for one patient, oldest first, with diagnoses, prescriptions
    written, medications already being taken, imaging and vitals attached.

    Use this for "what happened over time" and "what was this patient
    prescribed" questions.
    """
    client, cfg = _client_and_cfg()
    job = client.query(
        f"""
        SELECT * FROM `{cfg.table('v_patient_timeline')}`
        WHERE mrn = @mrn
        ORDER BY encounter_date
        LIMIT {MAX_ROWS}
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("mrn", "STRING", patient_mrn)
        ]),
    )
    encounters = _rows(job)
    return {"mrn": patient_mrn, "encounter_count": len(encounters), "encounters": encounters}


def run_sql(query: str) -> dict:
    """Run a read-only BigQuery SELECT against the documented views.

    Use this for aggregate and cross-patient questions the other tools do not
    answer directly. Call get_schema() first. The query must be a single SELECT
    (or WITH) reading only the documented views; it is row-limited and refused
    if it would scan too much data.
    """
    # Guard first, connect second: a refused query should not cost a credential
    # lookup or a network round trip.
    cfg = load_config()
    try:
        safe_query = guard_sql(query, cfg)
    except SqlGuardError as exc:
        return {"status": "refused", "reason": str(exc), "rows": []}

    client = bigquery.Client(project=cfg.project_id)
    try:
        dry_run = client.query(
            safe_query,
            job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False),
        )
    except Exception as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}",
                "sql": safe_query, "rows": []}

    if dry_run.total_bytes_processed and dry_run.total_bytes_processed > MAX_SCAN_BYTES:
        return {
            "status": "refused",
            "reason": f"query would scan {dry_run.total_bytes_processed:,} bytes, "
                      f"over the {MAX_SCAN_BYTES:,} limit; add a filter",
            "rows": [],
        }

    try:
        rows = _rows(client.query(safe_query))
    except Exception as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}",
                "sql": safe_query, "rows": []}
    return {"status": "ok", "sql": safe_query, "row_count": len(rows), "rows": rows}
