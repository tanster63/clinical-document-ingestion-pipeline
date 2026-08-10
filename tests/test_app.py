import pytest
from fastapi.testclient import TestClient

from ingestion.app import app, ingest_object

ENV = {"GCP_PROJECT_ID": "test-project", "BQ_DATASET": "test_dataset",
       "GCS_BUCKET": "test-bucket", "PIPELINE_VERSION": "test"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def client():
    return TestClient(app)


class FakeBlob:
    def __init__(self, data):
        self._data = data

    def download_as_bytes(self):
        return self._data


class FakeBucket:
    def __init__(self, data, missing=False):
        self._data, self._missing = data, missing

    def blob(self, name):
        if self._missing:
            raise FileNotFoundError(name)
        return FakeBlob(self._data)


class FakeStorage:
    def __init__(self, data, missing=False):
        self._data, self._missing = data, missing

    def bucket(self, name):
        return FakeBucket(self._data, self._missing)


class FakeWarehouse:
    def __init__(self, fail=False):
        self.written, self.runs, self.fail = [], [], fail

    def write_document(self, doc):
        if self.fail:
            raise RuntimeError("bigquery unavailable")
        self.written.append(doc)
        return {"encounters": len(doc.encounters)}

    def record_run(self, run):
        self.runs.append(run)


def event(name="chart.pdf", event_type="google.cloud.storage.object.v1.finalized"):
    return {
        "specversion": "1.0", "type": event_type,
        "source": "//storage.googleapis.com/projects/_/buckets/b", "id": "1",
        "data": {"bucket": "b", "name": name, "generation": "1"},
    }


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ingest_object_writes_and_summarizes(cfg, sample_pdf_bytes):
    warehouse = FakeWarehouse()
    result = ingest_object(
        bucket="b", object_name="incoming/chart.pdf", generation="1",
        trigger_source="manual", cfg=cfg, warehouse=warehouse,
        storage_client=FakeStorage(sample_pdf_bytes), llm_client=None,
    )
    assert result["status"] == "succeeded"
    assert result["encounters"] == 2
    assert result["gcs_uri"] == "gs://b/incoming/chart.pdf"
    assert len(warehouse.written) == 1
    assert warehouse.runs[0].trigger_source == "manual"
    assert warehouse.runs[0].status == "succeeded"
    assert warehouse.runs[0].encounters_written == 2


def test_the_object_prefix_is_not_mistaken_for_part_of_the_file_name(cfg, sample_pdf_bytes):
    """The MRN cross-check reads the file name, not the whole object path."""
    warehouse = FakeWarehouse()
    ingest_object(
        bucket="b",
        object_name="incoming/EMA_20250723T140400_0000_MRN4820917_PMS4820917"
                    "_PID18442091_PatientChart_400112.pdf",
        generation="1", trigger_source="manual", cfg=cfg, warehouse=warehouse,
        storage_client=FakeStorage(sample_pdf_bytes), llm_client=None,
    )
    document = warehouse.written[0].document
    assert document.mrn_from_filename == "4820917"
    assert "/" not in document.file_name


def test_a_warehouse_failure_is_recorded_and_still_returns_200(
        sample_pdf_bytes, monkeypatch):
    warehouse = FakeWarehouse(fail=True)
    monkeypatch.setattr("ingestion.app._storage_client",
                        lambda: FakeStorage(sample_pdf_bytes))
    monkeypatch.setattr("ingestion.app._warehouse", lambda cfg: warehouse)
    monkeypatch.setattr("ingestion.app._llm_client", lambda cfg: None)

    response = TestClient(app).post("/events", json=event())
    assert response.status_code == 200          # never retry a deterministic failure
    assert response.json()["status"] == "failed"
    assert warehouse.runs[0].status == "failed"
    assert "bigquery unavailable" in warehouse.runs[0].error_detail


def test_non_pdf_objects_are_acknowledged_and_skipped(client):
    response = client.post("/events", json=event(name="notes.txt"))
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"


def test_an_unrelated_event_type_is_acknowledged_and_skipped(client):
    response = client.post("/events", json=event(event_type="google.cloud.storage.object.v1.deleted"))
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"


def test_a_malformed_event_is_acknowledged_not_retried(client):
    response = client.post("/events", json={"nonsense": True})
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"


def test_manual_ingest_requires_an_object_name(client):
    assert client.post("/ingest", json={"bucket": "b"}).status_code == 422


def test_manual_ingest_surfaces_failures_as_non_2xx(monkeypatch):
    """Unlike /events, a human caller should see a real error code."""
    warehouse = FakeWarehouse()
    monkeypatch.setattr("ingestion.app._storage_client",
                        lambda: FakeStorage(b"", missing=True))
    monkeypatch.setattr("ingestion.app._warehouse", lambda cfg: warehouse)
    response = TestClient(app).post("/ingest", json={"bucket": "b", "object": "gone.pdf"})
    assert response.status_code == 500
    assert warehouse.runs[0].status == "failed"


def test_an_unwritable_audit_row_does_not_mask_the_real_outcome(cfg, sample_pdf_bytes):
    class Broken(FakeWarehouse):
        def record_run(self, run):
            raise RuntimeError("audit table missing")

    result = ingest_object(
        bucket="b", object_name="chart.pdf", generation="1", trigger_source="manual",
        cfg=cfg, warehouse=Broken(), storage_client=FakeStorage(sample_pdf_bytes),
        llm_client=None,
    )
    assert result["status"] == "succeeded"
