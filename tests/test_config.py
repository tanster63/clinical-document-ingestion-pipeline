"""Tests for configuration module."""

import pytest

from ingestion.config import Config, load_config


def test_load_config_reads_required_values():
    cfg = load_config({
        "GCP_PROJECT_ID": "proj-x",
        "BQ_DATASET": "cumberland",
        "GCS_BUCKET": "proj-x-charts-raw",
    })
    assert isinstance(cfg, Config)
    assert cfg.project_id == "proj-x"
    assert cfg.dataset == "cumberland"
    assert cfg.bucket == "proj-x-charts-raw"


def test_load_config_applies_defaults():
    cfg = load_config({
        "GCP_PROJECT_ID": "proj-x",
        "BQ_DATASET": "cumberland",
        "GCS_BUCKET": "b",
    })
    assert cfg.location == "us-central1"
    assert cfg.gemini_model.startswith("gemini-")
    assert cfg.pipeline_version


def test_load_config_overrides_defaults():
    cfg = load_config({
        "GCP_PROJECT_ID": "p", "BQ_DATASET": "d", "GCS_BUCKET": "b",
        "GCP_LOCATION": "us-east4", "GEMINI_MODEL": "gemini-2.5-pro",
        "PIPELINE_VERSION": "9.9.9",
    })
    assert cfg.location == "us-east4"
    assert cfg.gemini_model == "gemini-2.5-pro"
    assert cfg.pipeline_version == "9.9.9"


def test_load_config_names_every_missing_variable():
    with pytest.raises(ValueError) as exc:
        load_config({"GCP_PROJECT_ID": "p"})
    message = str(exc.value)
    assert "BQ_DATASET" in message
    assert "GCS_BUCKET" in message


def test_load_config_rejects_empty_required_values():
    """Empty required values are treated as missing."""
    with pytest.raises(ValueError) as exc:
        load_config({
            "GCP_PROJECT_ID": "",
            "BQ_DATASET": "d",
            "GCS_BUCKET": "b",
        })
    message = str(exc.value)
    assert "GCP_PROJECT_ID" in message


def test_config_is_immutable():
    cfg = load_config({
        "GCP_PROJECT_ID": "p",
        "BQ_DATASET": "d",
        "GCS_BUCKET": "b",
    })
    with pytest.raises(Exception):
        cfg.project_id = "other"  # type: ignore[misc]
