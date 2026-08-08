"""Tests for configuration module."""

import pytest

from ingestion.config import Config, load_config


def test_load_config_defaults():
    """Load config with required vars only, use defaults for optional."""
    cfg = load_config({
        "GCP_PROJECT_ID": "my-project",
        "BQ_DATASET": "my_dataset",
        "GCS_BUCKET": "my-bucket",
    })
    assert cfg.project_id == "my-project"
    assert cfg.dataset == "my_dataset"
    assert cfg.bucket == "my-bucket"
    assert cfg.location == "us-central1"
    assert cfg.gemini_model.startswith("gemini-")
    assert cfg.pipeline_version


def test_load_config_overrides_defaults():
    """Override defaults with env vars."""
    cfg = load_config({
        "GCP_PROJECT_ID": "p",
        "BQ_DATASET": "d",
        "GCS_BUCKET": "b",
        "GCP_LOCATION": "us-east4",
        "GEMINI_MODEL": "gemini-2.5-pro",
        "PIPELINE_VERSION": "9.9.9",
    })
    assert cfg.location == "us-east4"
    assert cfg.gemini_model == "gemini-2.5-pro"
    assert cfg.pipeline_version == "9.9.9"


def test_load_config_names_every_missing_variable():
    """Raise ValueError naming every required variable that is missing."""
    with pytest.raises(ValueError) as exc:
        load_config({"GCP_PROJECT_ID": "p"})
    message = str(exc.value)
    assert "BQ_DATASET" in message
    assert "GCS_BUCKET" in message


def test_config_is_immutable():
    """Config frozen dataclass cannot be reassigned."""
    cfg = load_config({
        "GCP_PROJECT_ID": "p",
        "BQ_DATASET": "d",
        "GCS_BUCKET": "b",
    })
    with pytest.raises(Exception):
        cfg.project_id = "other"  # type: ignore[misc]
