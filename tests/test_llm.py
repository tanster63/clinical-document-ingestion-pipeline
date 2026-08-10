import json
from types import SimpleNamespace

import pytest

from ingestion.extract.llm import ProseFacts, classify_encounter

GOOD = {
    "body_region": "knee", "laterality": "right", "visit_type": "follow_up",
    "hpi_summary": "Right knee osteoarthritis, improving on meloxicam.",
    "confidence": 0.93,
}


class FakeModels:
    def __init__(self, payload, raises=None):
        self.payload, self.raises, self.calls = payload, raises, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return SimpleNamespace(text=json.dumps(self.payload))


def fake_client(payload=None, raises=None):
    return SimpleNamespace(models=FakeModels(payload or GOOD, raises))


def test_classify_encounter_returns_the_four_columns(cfg):
    facts, issues = classify_encounter("Knee pain", "HPI text", "Plan text", cfg,
                                       client=fake_client())
    assert isinstance(facts, ProseFacts)
    assert facts.body_region == "knee"
    assert facts.laterality == "right"
    assert facts.visit_type == "follow_up"
    assert facts.hpi_summary.startswith("Right knee osteoarthritis")
    assert facts.confidence == pytest.approx(0.93)
    assert facts.model == cfg.gemini_model
    assert issues == []


def test_temperature_is_zero_and_the_schema_is_enforced(cfg):
    client = fake_client()
    classify_encounter("cc", "hpi", "note", cfg, client=client)
    config = client.models.calls[0]["config"]
    assert config.temperature == 0
    assert config.response_mime_type == "application/json"
    assert config.response_schema is not None


def test_api_failure_degrades_instead_of_raising(cfg):
    facts, issues = classify_encounter("cc", "hpi", "note", cfg,
                                       client=fake_client(raises=RuntimeError("503")))
    assert facts.body_region is None
    assert facts.hpi_summary is None
    assert [i.severity for i in issues] == ["error"]
    assert issues[0].issue_type == "llm_failed"


def test_unparseable_response_degrades(cfg):
    client = SimpleNamespace(models=FakeModels(None))
    client.models.generate_content = lambda **kw: SimpleNamespace(text="not json")
    facts, issues = classify_encounter("cc", "hpi", "note", cfg, client=client)
    assert facts.body_region is None
    assert issues[0].issue_type == "llm_failed"


def test_low_confidence_is_recorded_as_a_warning_but_the_value_is_kept(cfg):
    facts, issues = classify_encounter("cc", "hpi", "note", cfg,
                                       client=fake_client(dict(GOOD, confidence=0.4)))
    assert facts.body_region == "knee"
    assert [i.issue_type for i in issues] == ["low_confidence"]
    assert issues[0].severity == "warn"


def test_out_of_vocabulary_values_are_rejected(cfg):
    payload = dict(GOOD, laterality="dorsal", visit_type="telehealth")
    facts, issues = classify_encounter("cc", "hpi", "note", cfg, client=fake_client(payload))
    assert facts.laterality is None
    assert facts.visit_type is None
    assert facts.body_region == "knee"
    assert {i.field_name for i in issues} == {"laterality", "visit_type"}


def test_laterality_none_becomes_null_not_the_string_none(cfg):
    """A region with no side stores NULL, so the column keeps one meaning."""
    facts, _ = classify_encounter("cc", "hpi", "note", cfg,
                                  client=fake_client(dict(GOOD, laterality="none")))
    assert facts.laterality is None


def test_empty_input_skips_the_call_entirely(cfg):
    client = fake_client()
    facts, issues = classify_encounter("", "", "", cfg, client=client)
    assert client.models.calls == []
    assert facts.body_region is None
    assert issues[0].issue_type == "missing_section"
