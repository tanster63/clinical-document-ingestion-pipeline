"""Shared test fixtures."""

import os
from pathlib import Path

import pytest

from ingestion.config import Config

# The agent package builds its LlmAgent from configuration at import time, which
# is correct for a deployed service but means the environment has to exist
# before pytest can even collect the agent tests. Defaults are only applied
# where nothing real is set, so a sourced .env always wins.
for _name, _value in (("GCP_PROJECT_ID", "test-project"),
                      ("BQ_DATASET", "test_dataset"),
                      ("GCS_BUCKET", "test-bucket"),
                      ("GEMINI_MODEL", "gemini-2.5-flash")):
    os.environ.setdefault(_name, _value)


REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CHART = (
    REPO_ROOT
    / "charts/source"
    / "EMA_20250723T140400_0000_MRN4820917_PMS4820917_PID18442091_PatientChart_400112.pdf"
)


GENERATED_CHARTS = REPO_ROOT / "charts/generated"


def _config() -> Config:
    return Config(
        project_id="test-project",
        dataset="test_dataset",
        bucket="test-bucket",
        location="us-central1",
        gemini_model="gemini-2.5-flash",
        pipeline_version="test",
    )


@pytest.fixture
def cfg() -> Config:
    """Config with obviously-fake values, never touches GCP."""
    return _config()


@pytest.fixture(scope="module")
def module_cfg() -> Config:
    """The same fake Config, for fixtures that extract a chart once per module."""
    return _config()


@pytest.fixture(scope="module")
def generated_pdfs() -> list[Path]:
    paths = sorted(GENERATED_CHARTS.glob("*.pdf"))
    if not paths:
        pytest.skip("rendered corpus missing; run python -m corpus.render")
    return paths


@pytest.fixture(scope="module")
def sample_pdf_bytes() -> bytes:
    """Read the provided sample chart PDF.

    Skips the test if the file is missing — we never commit it.
    """
    if not SAMPLE_CHART.exists():
        pytest.skip(f"provided sample chart not found at {SAMPLE_CHART}")

    return SAMPLE_CHART.read_bytes()
