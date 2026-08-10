"""The only LLM in the pipeline.

Scope is deliberately narrow: four columns on `encounters`, derived from
prose that has no deterministic structure to parse. Identifiers, dates, ICD-10
codes, prescriptions, vitals, provider, and follow-up intervals are never routed
here, because a wrong value in any of them is unacceptable and a model cannot
promise not to produce one.

Every failure mode degrades to NULL plus an issue row. A missing classification
costs four nullable columns; a failed ingest costs the whole chart.
"""

import json
from dataclasses import dataclass

from ingestion.config import Config
from ingestion.issues import IssueDraft, error, warn

BODY_REGIONS = ["shoulder", "elbow", "wrist", "hand", "hip", "knee", "ankle", "foot",
                "cervical spine", "thoracic spine", "lumbar spine", "other"]
LATERALITIES = ["left", "right", "bilateral", "none"]
VISIT_TYPES = ["new", "follow_up", "post_op"]
CONFIDENCE_FLOOR = 0.6

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "body_region": {"type": "STRING", "enum": BODY_REGIONS},
        "laterality": {"type": "STRING", "enum": LATERALITIES},
        "visit_type": {"type": "STRING", "enum": VISIT_TYPES},
        "hpi_summary": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
    },
    "required": ["body_region", "laterality", "visit_type", "hpi_summary", "confidence"],
}

PROMPT = """You are classifying one orthopedic clinic encounter.

Use only the text provided. Do not infer anything the text does not state.
- body_region: the anatomic region this visit is about.
- laterality: the side, or "none" if the region has no side or the text does not say.
- visit_type: "new" for a first presentation of this problem, "follow_up" for a
  return visit for a problem already being managed, "post_op" if the text
  describes care after a procedure the patient has already had.
- hpi_summary: one sentence, under 200 characters, factual, no speculation.
- confidence: 0.0-1.0, your confidence in body_region, laterality and visit_type.

CHIEF COMPLAINT:
{chief_complaint}

HISTORY OF PRESENT ILLNESS:
{hpi_text}

PLAN / NOTE:
{note_text}
"""


@dataclass(frozen=True)
class ProseFacts:
    body_region: str | None = None
    laterality: str | None = None
    visit_type: str | None = None
    hpi_summary: str | None = None
    confidence: float | None = None
    model: str | None = None


EMPTY_PROSE_FACTS = ProseFacts()


def build_client(cfg: Config):
    """A Vertex AI client. Imported lazily so unit tests never need the SDK."""
    from google import genai

    return genai.Client(vertexai=True, project=cfg.project_id, location=cfg.location)


def classify_encounter(
    chief_complaint: str,
    hpi_text: str,
    note_text: str,
    cfg: Config,
    client=None,
) -> tuple[ProseFacts, list[IssueDraft]]:
    if not any(part.strip() for part in (chief_complaint, hpi_text, note_text)):
        return EMPTY_PROSE_FACTS, [warn(
            "missing_section",
            "no chief complaint, HPI, or note text; skipped the classification call",
            field_name="hpi_summary",
        )]

    from google.genai import types

    client = client or build_client(cfg)
    request_config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
    )
    try:
        response = client.models.generate_content(
            model=cfg.gemini_model,
            contents=PROMPT.format(
                chief_complaint=chief_complaint or "(none recorded)",
                hpi_text=hpi_text or "(none recorded)",
                note_text=note_text or "(none recorded)",
            ),
            config=request_config,
        )
        payload = json.loads(response.text)
    except Exception as exc:  # network, quota, schema violation, bad JSON
        return EMPTY_PROSE_FACTS, [error(
            "llm_failed", f"{type(exc).__name__}: {exc}", field_name="hpi_summary"
        )]

    issues: list[IssueDraft] = []

    def vetted(field: str, allowed: list[str]) -> str | None:
        value = payload.get(field)
        if value in allowed:
            return None if value == "none" and field == "laterality" else value
        if value is not None:
            issues.append(warn(
                "validation_failed",
                f"model returned {field}={value!r}, which is outside the allowed set",
                field_name=field,
            ))
        return None

    body_region = vetted("body_region", BODY_REGIONS)
    laterality = vetted("laterality", LATERALITIES)
    visit_type = vetted("visit_type", VISIT_TYPES)

    confidence = payload.get("confidence")
    confidence = float(confidence) if isinstance(confidence, (int, float)) else None
    if confidence is not None and confidence < CONFIDENCE_FLOOR:
        issues.append(warn(
            "low_confidence",
            f"model confidence {confidence:.2f} below {CONFIDENCE_FLOOR}",
            field_name="body_region",
        ))

    summary = payload.get("hpi_summary")
    return ProseFacts(
        body_region=body_region,
        laterality=laterality,
        visit_type=visit_type,
        hpi_summary=summary.strip() if isinstance(summary, str) and summary.strip() else None,
        confidence=confidence,
        model=cfg.gemini_model,
    ), issues
