"""Shared test fixtures."""

from pathlib import Path

import pytest

from ingestion.config import Config


REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CHART = (
    REPO_ROOT
    / "charts/source"
    / "EMA_20250723T140400_0000_MRN4820917_PMS4820917_PID18442091_PatientChart_400112.pdf"
)


@pytest.fixture
def cfg() -> Config:
    """Config with obviously-fake values, never touches GCP."""
    return Config(
        project_id="test-project",
        dataset="test_dataset",
        bucket="test-bucket",
        location="us-central1",
        gemini_model="gemini-2.5-flash",
        pipeline_version="test",
    )


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Read the provided sample chart PDF.

    Skips the test if the file is missing — we never commit it.
    """
    if not SAMPLE_CHART.exists():
        pytest.skip(f"provided sample chart not found at {SAMPLE_CHART}")

    return SAMPLE_CHART.read_bytes()
