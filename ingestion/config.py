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
    """Immutable configuration for the pipeline.

    All deployment details (project ID, bucket, dataset) come from environment,
    never from source code. No value here appears in version control.
    """

    project_id: str
    dataset: str
    bucket: str
    location: str
    gemini_model: str
    pipeline_version: str


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Load configuration from environment variables.

    Args:
        env: Mapping of environment variables. Defaults to os.environ if None.

    Returns:
        Config object with validated values.

    Raises:
        ValueError: If any required variable is missing, naming all of them.
    """
    source = env if env is not None else os.environ

    # Validate all required vars are present
    missing = [var for var in REQUIRED_VARS if var not in source]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return Config(
        project_id=source["GCP_PROJECT_ID"],
        dataset=source["BQ_DATASET"],
        bucket=source["GCS_BUCKET"],
        location=source.get("GCP_LOCATION") or DEFAULT_LOCATION,
        gemini_model=source.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
        pipeline_version=source.get("PIPELINE_VERSION") or DEFAULT_PIPELINE_VERSION,
    )
