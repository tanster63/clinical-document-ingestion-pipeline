"""Environment-driven configuration. No deployment literal appears anywhere else."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

REQUIRED_VARS = ("GCP_PROJECT_ID", "BQ_DATASET", "GCS_BUCKET")

DEFAULT_LOCATION = "us-central1"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_PIPELINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class Config:
    """Everything the pipeline needs to know about where it is running."""

    project_id: str
    dataset: str
    bucket: str
    location: str
    gemini_model: str
    pipeline_version: str

    @property
    def dataset_ref(self) -> str:
        return f"{self.project_id}.{self.dataset}"

    def table(self, name: str) -> str:
        return f"{self.project_id}.{self.dataset}.{name}"


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a Config from a mapping, defaulting to the process environment.

    Raises ValueError naming every missing variable at once, so a misconfigured
    deploy fails on the first request with a complete message instead of one
    variable per attempt.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    missing = [name for name in REQUIRED_VARS if not source.get(name)]
    if missing:
        raise ValueError(
            "missing required environment variables: " + ", ".join(sorted(missing))
        )
    return Config(
        project_id=source["GCP_PROJECT_ID"],
        dataset=source["BQ_DATASET"],
        bucket=source["GCS_BUCKET"],
        location=source.get("GCP_LOCATION") or DEFAULT_LOCATION,
        gemini_model=source.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
        pipeline_version=source.get("PIPELINE_VERSION") or DEFAULT_PIPELINE_VERSION,
    )
