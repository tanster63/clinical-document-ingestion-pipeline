"""Run the whole pipeline over every chart without touching Google Cloud.

The deployed path is GCS -> Cloud Run -> BigQuery. This runs the identical
extraction code against the charts on disk and applies the *same* merge
contract — `rows_for()` and `MERGE_KEYS`, the declarations BigQuery itself is
driven by — into an in-memory warehouse. Nothing here re-implements the parsing
or the keys, so what it proves about idempotency is a property of the shipped
code rather than of this script.

What it does not prove is the parts that are BigQuery's behaviour rather than
ours: load-job visibility to MERGE, and the streaming-buffer caveat that is the
reason a load job is used at all. `tests/test_warehouse_live.py` covers those,
and needs credentials.

    python scripts/run_local.py                 # summary only
    python scripts/run_local.py --out build/local_warehouse
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ingestion.config import Config  # noqa: E402
from ingestion.extract.pipeline import extract_document  # noqa: E402
from ingestion.warehouse import MERGE_KEYS, rows_for  # noqa: E402

SOURCE_DIR = REPO_ROOT / "charts/source"
GENERATED_DIR = REPO_ROOT / "charts/generated"

LOCAL_CONFIG = Config(
    project_id="local", dataset="cumberland", bucket="local",
    location="local", gemini_model="(not called)", pipeline_version="local",
)


class LocalWarehouse:
    """Tables keyed exactly as `MERGE_KEYS` declares, so a second write of the
    same chart updates rows instead of adding them."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[tuple, dict]] = {}
        self.merges = 0
        self.inserts = 0

    def write_document(self, doc) -> None:
        for table, rows in rows_for(doc).items():
            target = self.tables.setdefault(table, {})
            for row in rows:
                key = tuple(row[column] for column in MERGE_KEYS[table])
                if key in target:
                    self.merges += 1
                else:
                    self.inserts += 1
                target[key] = row

    def counts(self) -> dict[str, int]:
        return {table: len(rows) for table, rows in sorted(self.tables.items())}

    def dump(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for table, rows in self.tables.items():
            path = out_dir / f"{table}.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows.values()))


def charts() -> list[Path]:
    return sorted(SOURCE_DIR.glob("*.pdf")) + sorted(GENERATED_DIR.glob("*.pdf"))


def ingest_all(warehouse: LocalWarehouse, paths: list[Path]) -> list:
    """Every chart, independently. One unreadable file costs its own row and
    nothing else — the same posture the deployed service takes."""
    documents = []
    for path in paths:
        try:
            doc = extract_document(path.read_bytes(), file_name=path.name,
                                   cfg=LOCAL_CONFIG, gcs_uri=f"file://{path}")
            warehouse.write_document(doc)
        except Exception as exc:                      # noqa: BLE001 - reported, not raised
            print(f"  ! {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            documents.append(None)
            continue
        documents.append(doc)
    return documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None,
                        help="write one JSONL file per table to this directory")
    args = parser.parse_args()

    paths = charts()
    if not paths:
        print("no charts found under charts/", file=sys.stderr)
        return 1

    warehouse = LocalWarehouse()
    documents = ingest_all(warehouse, paths)

    print(f"{'chart':<40} {'enc':>4} {'dx':>4} {'rx':>4} {'med':>4} {'img':>4} "
          f"{'vit':>4} {'exam':>5}  status")
    for path, doc in zip(paths, documents):
        if doc is None:
            print(f"{path.name[:40]:<40} {'-':>4} {'-':>4} {'-':>4} {'-':>4} {'-':>4} "
                  f"{'-':>4} {'-':>5}  unreadable")
            continue
        print(f"{path.name[:40]:<40} {len(doc.encounters):>4} {len(doc.diagnoses):>4} "
              f"{len(doc.prescriptions):>4} {len(doc.medications):>4} "
              f"{len(doc.imaging):>4} {len(doc.vitals):>4} {len(doc.exam_findings):>5}  "
              f"{doc.document.parse_status}")

    print("\nwarehouse after one pass")
    first = warehouse.counts()
    for table, count in first.items():
        print(f"  {table:<24} {count:>5}")

    landed = [doc for doc in documents if doc is not None]
    warnings = [i for doc in landed for i in doc.issues if i.severity == "warn"]
    errors = [i for doc in landed for i in doc.issues if i.severity == "error"]
    print(f"\nissues recorded: {len(warnings)} warn, {len(errors)} error")
    for issue in warnings:
        print(f"  warn  {issue.issue_type:<16} {issue.field_name or '-':<14} {issue.detail}")
    for issue in errors:
        print(f"  ERROR {issue.issue_type:<16} {issue.field_name or '-':<14} {issue.detail}")

    # Idempotency: the same charts again, then the same charts under new names.
    ingest_all(warehouse, paths)
    after_repeat = warehouse.counts()
    renamed = LocalWarehouse()
    ingest_all(renamed, paths)
    for path in paths:
        doc = extract_document(path.read_bytes(), file_name=f"re-export-{path.name}",
                               cfg=LOCAL_CONFIG, gcs_uri=f"file://re-export/{path.name}")
        renamed.write_document(doc)
    clinical = {t: n for t, n in renamed.counts().items() if t != "documents"}

    print("\nidempotency")
    print(f"  re-ingesting all {len(paths)} charts: "
          f"{'row counts unchanged' if after_repeat == first else 'CHANGED'}")
    same = all(clinical.get(t) == first.get(t) for t in clinical if t != "ingestion_issues")
    print(f"  re-exporting them under new file names: "
          f"{'clinical row counts unchanged' if same else 'CHANGED'}")

    if args.out:
        warehouse.dump(args.out)
        print(f"\nwrote {len(first)} tables to {args.out}/")

    return 0 if not errors and after_repeat == first and same else 1


if __name__ == "__main__":
    raise SystemExit(main())
