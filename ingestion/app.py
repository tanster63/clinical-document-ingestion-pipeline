"""Cloud Run ingest service.

Two entry points onto one code path (§3):

  POST /ingest  — manual, for backfill and demos; failures return 5xx so a human
                  caller sees a real error.
  POST /events  — Eventarc object.finalized; **always** returns 2xx.

The asymmetry is deliberate. A non-2xx to Eventarc means redelivery, and a chart
that fails deterministically would then retry forever against a live billing
account. Failures are acknowledged and recorded in `ingest_runs` and
`ingestion_issues`, where they are queryable, rather than re-thrown into a loop.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ingestion.config import Config, load_config
from ingestion.extract.pipeline import extract_document
from ingestion.models import IngestRun
from ingestion.warehouse import Warehouse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ingest")

app = FastAPI(title="Clinical Document Ingestion", version="1.0")
SUPPORTED_SUFFIXES = (".pdf",)
FINALIZED_EVENT = "google.cloud.storage.object.v1.finalized"


class IngestRequest(BaseModel):
    object: str
    bucket: str | None = None
    generation: str | None = None


def _config() -> Config:
    return load_config()


def _storage_client():
    from google.cloud import storage

    return storage.Client()


def _warehouse(cfg: Config) -> Warehouse:
    return Warehouse(cfg)


def _llm_client(cfg: Config):
    from ingestion.extract.llm import build_client

    return build_client(cfg)


def ingest_object(
    *,
    bucket: str | None,
    object_name: str,
    generation: str | None,
    trigger_source: str,
    cfg: Config,
    warehouse=None,
    storage_client=None,
    llm_client="auto",
) -> dict:
    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc)
    bucket = bucket or cfg.bucket
    gcs_uri = f"gs://{bucket}/{object_name}"
    warehouse = warehouse or _warehouse(cfg)

    def finish(status: str, **extra) -> dict:
        try:
            warehouse.record_run(IngestRun(
                run_id=run_id, document_id=extra.get("document_id"), gcs_uri=gcs_uri,
                trigger_source=trigger_source, status=status,
                started_at=started, finished_at=datetime.now(timezone.utc),
                encounters_written=extra.get("encounters", 0),
                issues_warn=extra.get("warnings", 0), issues_error=extra.get("errors", 0),
                pipeline_version=cfg.pipeline_version, error_detail=extra.get("detail"),
            ))
        except Exception:  # the audit row must never mask the real outcome
            log.exception("could not record ingest run %s", run_id)
        return {"run_id": run_id, "status": status, "gcs_uri": gcs_uri, **extra}

    try:
        client = storage_client or _storage_client()
        pdf_bytes = client.bucket(bucket).blob(object_name).download_as_bytes()

        if llm_client == "auto":
            llm_client = _llm_client(cfg)
        doc = extract_document(
            pdf_bytes, file_name=object_name.rsplit("/", 1)[-1], cfg=cfg,
            gcs_uri=gcs_uri, llm_client=llm_client, run_id=run_id,
        )
        warehouse.write_document(doc)
    except Exception as exc:
        log.exception("ingest failed for %s", gcs_uri)
        return finish("failed", detail=f"{type(exc).__name__}: {exc}")

    warnings = sum(1 for i in doc.issues if i.severity == "warn")
    errors = sum(1 for i in doc.issues if i.severity == "error")
    return finish(
        "partial" if errors else "succeeded",
        document_id=doc.document.document_id,
        encounters=len(doc.encounters), warnings=warnings, errors=errors,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "pipeline_version": _config().pipeline_version}


@app.post("/ingest")
def ingest(request: IngestRequest) -> dict:
    result = ingest_object(
        bucket=request.bucket, object_name=request.object,
        generation=request.generation, trigger_source="manual", cfg=_config(),
    )
    if result["status"] == "failed":
        raise HTTPException(status_code=500, detail=result.get("detail", "ingest failed"))
    return result


@app.post("/events")
def events(envelope: dict) -> dict:
    """Eventarc receiver. Every path returns 200 — see the module docstring."""
    data = envelope.get("data") or {}
    object_name = data.get("name")
    event_type = envelope.get("type")

    if event_type != FINALIZED_EVENT or not object_name:
        log.warning("ignoring event: type=%s object=%s", event_type, object_name)
        return {"status": "skipped", "reason": "unsupported event"}

    if not object_name.lower().endswith(SUPPORTED_SUFFIXES):
        log.info("ignoring non-PDF object %s", object_name)
        return {"status": "skipped", "reason": "not a PDF"}

    try:
        return ingest_object(
            bucket=data.get("bucket"), object_name=object_name,
            generation=str(data["generation"]) if data.get("generation") else None,
            trigger_source="eventarc", cfg=_config(),
        )
    except Exception as exc:  # last-resort net: still acknowledge
        log.exception("unhandled error on %s", object_name)
        return {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
