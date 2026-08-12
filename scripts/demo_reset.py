#!/usr/bin/env python3
"""Clear one chart's rows so the demo can show them arrive.

The §6.6 video has to show a PDF landing in the bucket and rows appearing in
BigQuery. Every chart is already ingested, and the pipeline is idempotent by
design, so re-uploading one produces an audit row and no clinical rows -- which
is the right behaviour and the wrong picture. This clears a single document's
rows first so the ingest that follows has something to write.

Nothing here fabricates data. It deletes rows the pipeline will reproduce
byte-for-byte from the same PDF, which is the idempotency claim stated as a
procedure rather than as a sentence. `--verify` is what proves it: it compares
the warehouse against the snapshot taken before the delete and reports any
column that came back different.

    python scripts/demo_reset.py --clear            # before recording
    ... upload the chart, let Eventarc ingest it ...
    python scripts/demo_reset.py --verify           # after recording

    python scripts/demo_reset.py --restore          # if the ingest never ran

The backup is a set of `_demo_bak_*` tables in the same dataset, so `--restore`
puts the warehouse back without needing the pipeline at all. `--verify` drops
them once it is satisfied; until then they stay, because a backup you delete
before checking the restore is not a backup.

Audit tables are deliberately untouched. `ingest_runs` and `ingestion_issues`
are append-only history: erasing the record of what the pipeline did in order
to film the pipeline doing it is the one thing this must not do.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from google.cloud import bigquery

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ingestion.config import load_config  # noqa: E402

# Every clinical table names the document it came from. The audit tables are
# absent on purpose -- see the module docstring.
CLINICAL_TABLES = [
    "patients", "encounters", "diagnoses", "prescriptions", "imaging_studies",
    "procedures", "exam_findings", "patient_history", "medication_snapshots",
    "vitals",
]

SNAPSHOT = pathlib.Path(__file__).resolve().parent.parent / "build" / "demo_reset_state.json"


def _resolve_document(client: bigquery.Client, cfg, needle: str) -> tuple[str, str]:
    """Find the one document whose filename or id contains `needle`."""
    rows = list(client.query(
        f"SELECT document_id, file_name FROM `{cfg.table('documents')}` "
        "WHERE STRPOS(file_name, @needle) > 0 OR STRPOS(document_id, @needle) > 0",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("needle", "STRING", needle)]),
    ).result())
    if not rows:
        sys.exit(f"no document matches {needle!r}")
    if len(rows) > 1:
        names = ", ".join(r["file_name"] for r in rows)
        sys.exit(f"{needle!r} matches {len(rows)} documents ({names}); be more specific")
    return rows[0]["document_id"], rows[0]["file_name"]


def _counts(client: bigquery.Client, cfg, document_id: str) -> dict[str, int]:
    """Row counts for this document, per table."""
    parts = [f"SELECT '{t}' AS t, COUNT(*) AS n FROM `{cfg.table(t)}` "
             f"WHERE source_document_id = @doc" for t in CLINICAL_TABLES]
    parts.append(f"SELECT 'documents', COUNT(*) FROM `{cfg.table('documents')}` "
                 "WHERE document_id = @doc")
    rows = client.query(
        " UNION ALL ".join(parts),
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("doc", "STRING", document_id)]),
    ).result()
    return {row["t"]: row["n"] for row in rows}


def _fingerprint(client: bigquery.Client, cfg, document_id: str) -> dict[str, str]:
    """A hash of every row of this document, per table.

    Counts alone would let a re-ingest that changed a value pass as a restore.
    Hashing the sorted row JSON catches a changed column, and ordering by the
    hash rather than by a key makes the fingerprint independent of row order.
    """
    out = {}
    for table in CLINICAL_TABLES:
        row = list(client.query(
            f"SELECT TO_HEX(MD5(STRING_AGG(h, '' ORDER BY h))) AS fp FROM ("
            f"  SELECT TO_HEX(MD5(TO_JSON_STRING(t))) AS h FROM `{cfg.table(table)}` t"
            f"  WHERE source_document_id = @doc)",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("doc", "STRING", document_id)]),
        ).result())[0]
        out[table] = row["fp"] or ""
    return out


def _backup_name(cfg, table: str) -> str:
    return cfg.table(f"_demo_bak_{table}")


def clear(client: bigquery.Client, cfg, needle: str, dry_run: bool = False) -> None:
    document_id, file_name = _resolve_document(client, cfg, needle)
    before = _counts(client, cfg, document_id)
    fingerprint = _fingerprint(client, cfg, document_id)
    total = sum(before.values())
    if total == 0:
        sys.exit(f"{file_name} has no rows to clear; nothing to do")

    print(f"document  {file_name}")
    print(f"          {document_id[:16]}...")
    verb = "would clear" if dry_run else "clearing"
    print(f"{verb:9} {total} rows across {sum(1 for n in before.values() if n)} tables\n")

    if dry_run:
        for table in CLINICAL_TABLES + ["documents"]:
            if before.get(table):
                print(f"  {table:22} {before[table]:>4} rows"
                      f"   fp {fingerprint.get(table, '')[:12] or '-'}")
        print("\ndry run: nothing was deleted and no backup was written.")
        return

    param = [bigquery.ScalarQueryParameter("doc", "STRING", document_id)]
    for table in CLINICAL_TABLES + ["documents"]:
        key = "document_id" if table == "documents" else "source_document_id"
        client.query(
            f"CREATE OR REPLACE TABLE `{_backup_name(cfg, table)}` AS "
            f"SELECT * FROM `{cfg.table(table)}` WHERE {key} = @doc",
            job_config=bigquery.QueryJobConfig(query_parameters=param),
        ).result()
        client.query(
            f"DELETE FROM `{cfg.table(table)}` WHERE {key} = @doc",
            job_config=bigquery.QueryJobConfig(query_parameters=param),
        ).result()
        if before.get(table):
            print(f"  {table:22} {before[table]:>4} rows cleared (backed up)")

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps({
        "document_id": document_id, "file_name": file_name,
        "counts": before, "fingerprint": fingerprint,
    }, indent=2))

    after = _counts(client, cfg, document_id)
    assert sum(after.values()) == 0, f"rows survived the delete: {after}"
    print(f"\nwarehouse is clear of this chart. state saved to {SNAPSHOT}")
    print(f"\nnow upload it and let Eventarc ingest:\n"
          f'  gcloud storage cp "charts/**/{file_name}" "gs://$GCS_BUCKET/incoming/"')


def _load_snapshot() -> dict:
    if not SNAPSHOT.exists():
        sys.exit(f"no snapshot at {SNAPSHOT}; run --clear first")
    return json.loads(SNAPSHOT.read_text())


def verify(client: bigquery.Client, cfg) -> None:
    state = _load_snapshot()
    document_id = state["document_id"]
    now = _counts(client, cfg, document_id)
    expected = state["counts"]

    print(f"document  {state['file_name']}\n")
    ok = True
    for table in sorted(expected):
        want, got = expected[table], now.get(table, 0)
        mark = "ok " if want == got else "BAD"
        if want != got:
            ok = False
        if want or got:
            print(f"  {mark} {table:22} expected {want:>4}   found {got:>4}")

    if ok:
        print("\nrow counts match. checking every column, not just the counts:")
        current = _fingerprint(client, cfg, document_id)
        for table in CLINICAL_TABLES:
            if current[table] != state["fingerprint"][table]:
                ok = False
                print(f"  BAD {table:22} rows returned with different values")
        if ok:
            print("  ok  every row is byte-identical to what was cleared")

    if not ok:
        print("\nthe re-ingest did not reproduce the chart. The backup tables are\n"
              "still there -- run --restore to put the warehouse back.")
        sys.exit(1)

    for table in CLINICAL_TABLES + ["documents"]:
        client.delete_table(_backup_name(cfg, table), not_found_ok=True)
    SNAPSHOT.unlink()
    print("\nbackups dropped. warehouse is exactly where it started.")


def restore(client: bigquery.Client, cfg) -> None:
    state = _load_snapshot()
    document_id = state["document_id"]
    param = [bigquery.ScalarQueryParameter("doc", "STRING", document_id)]
    print(f"restoring {state['file_name']} from backup\n")

    for table in CLINICAL_TABLES + ["documents"]:
        key = "document_id" if table == "documents" else "source_document_id"
        backup = _backup_name(cfg, table)
        # Delete first: a partial re-ingest may have written some of these rows
        # back already, and INSERT would then double them.
        client.query(f"DELETE FROM `{cfg.table(table)}` WHERE {key} = @doc",
                     job_config=bigquery.QueryJobConfig(query_parameters=param)).result()
        client.query(f"INSERT INTO `{cfg.table(table)}` "
                     f"SELECT * FROM `{backup}`").result()

    now = _counts(client, cfg, document_id)
    for table in sorted(state["counts"]):
        if state["counts"][table] or now.get(table, 0):
            print(f"  {table:22} {now.get(table, 0):>4} rows")
    assert now == state["counts"], f"restore did not match: {now} != {state['counts']}"
    print("\nrestored. backup tables and snapshot left in place.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--clear", action="store_true",
                       help="back up and delete one chart's rows")
    group.add_argument("--verify", action="store_true",
                       help="check the re-ingest reproduced them, then drop the backup")
    group.add_argument("--restore", action="store_true",
                       help="put the backed-up rows back without re-ingesting")
    parser.add_argument("--dry-run", action="store_true",
                       help="with --clear: report what would go, delete nothing")
    parser.add_argument("--chart", default="MRN4820917",
                        help="substring of the filename or document id "
                             "(default: the chart the brief provided)")
    args = parser.parse_args()

    cfg = load_config()
    client = bigquery.Client(project=cfg.project_id)

    if args.clear:
        clear(client, cfg, args.chart, dry_run=args.dry_run)
    elif args.verify:
        verify(client, cfg)
    else:
        restore(client, cfg)


if __name__ == "__main__":
    main()
