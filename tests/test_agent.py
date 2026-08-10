"""The agent's shape and its grounding contract.

No model is called here. What is worth testing without one is that the four
tools are actually wired up, that the model name comes from configuration
rather than a literal, and that the instruction still contains the rules that
keep answers tied to returned rows — those rules are the deliverable, and they
are easy to erode by accident while editing prose.
"""

import pytest

ENV = {"GCP_PROJECT_ID": "test-project", "BQ_DATASET": "test_dataset",
       "GCS_BUCKET": "test-bucket", "GEMINI_MODEL": "gemini-2.5-flash"}


@pytest.fixture(scope="module")
def agent_module():
    import os
    import importlib

    previous = {k: os.environ.get(k) for k in ENV}
    os.environ.update(ENV)
    try:
        module = importlib.import_module("agent.agent")
        yield importlib.reload(module)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_all_four_tools_are_registered(agent_module):
    registered = {getattr(t, "__name__", getattr(t, "name", ""))
                  for t in agent_module.root_agent.tools}
    assert registered == {"get_schema", "find_patient", "patient_timeline", "run_sql"}


def test_the_agent_is_pinned_to_the_configured_model(agent_module):
    assert agent_module.root_agent.model == ENV["GEMINI_MODEL"]


def test_no_deployment_literal_reaches_the_agent_source():
    source = (__import__("pathlib").Path("agent/agent.py").read_text()
              + __import__("pathlib").Path("agent/tools.py").read_text())
    for literal in ("test-project", "cumberland", "gemini-2.5-flash"):
        assert literal not in source


def test_the_instruction_forbids_answering_without_a_tool_call(agent_module):
    lowered = agent_module.INSTRUCTION.lower()
    assert "never answer from memory" in lowered
    assert "call a tool" in lowered


def test_the_instruction_requires_saying_when_data_is_absent(agent_module):
    assert "not recorded" in agent_module.INSTRUCTION.lower()


def test_the_instruction_names_the_preferred_name_trap(agent_module):
    assert "preferred name" in agent_module.INSTRUCTION.lower()


def test_the_instruction_states_the_medication_snapshot_caveat(agent_module):
    lowered = agent_module.INSTRUCTION.lower()
    assert "snapshot" in lowered
    assert "as of" in lowered


def test_the_instruction_marks_which_columns_a_model_produced(agent_module):
    lowered = agent_module.INSTRUCTION.lower()
    for column in ("body_region", "laterality", "visit_type", "hpi_summary"):
        assert column in lowered
    assert "model-derived" in lowered


def test_the_instruction_declines_clinical_advice(agent_module):
    assert "medical advice" in agent_module.INSTRUCTION.lower()
