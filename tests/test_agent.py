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


# --- the deployed package has a different name than the source directory ------
#
# `adk deploy cloud_run --app_name clinical_query_agent` copies ./agent to
# /app/agents/clinical_query_agent/. Any `from agent.x import ...` inside the
# package therefore resolves locally and fails in Cloud Run with
# ModuleNotFoundError: No module named 'agent' -- at request time, not at
# deploy time, so the service goes green and every question returns a 500.
# That cost a full build cycle to find once. These tests find it in a second.

AGENT_MODULES = ("__init__.py", "agent.py", "tools.py")


def test_the_package_never_imports_itself_by_its_source_directory_name():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "agent"
    for name in AGENT_MODULES:
        text = (root / name).read_text()
        for line in text.splitlines():
            stripped = line.strip()
            assert not stripped.startswith("from agent."), (
                f"{name}: {stripped!r} resolves here and breaks once ADK renames "
                "the package; use a relative import"
            )
            assert not stripped.startswith("import agent"), f"{name}: {stripped!r}"


def test_the_package_imports_under_the_name_adk_deploys_it_as(tmp_path, monkeypatch):
    """Copy the package to a different name and import it, as Cloud Run does."""
    import pathlib
    import shutil
    import sys

    repo = pathlib.Path(__file__).resolve().parent.parent
    target = tmp_path / "clinical_query_agent"
    shutil.copytree(repo / "agent", target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # deploy_agent.sh ships ingestion/config.py as the package's own _config
    shutil.copy(repo / "ingestion" / "config.py", target / "_config.py")

    monkeypatch.syspath_prepend(str(tmp_path))
    for module in [m for m in sys.modules if m.startswith("clinical_query_agent")]:
        del sys.modules[module]
    monkeypatch.delitem(sys.modules, "ingestion.config", raising=False)

    import importlib
    package = importlib.import_module("clinical_query_agent")
    assert package.root_agent.name == "clinical_query_agent"
    assert {tool.__name__ for tool in package.root_agent.tools} == {
        "get_schema", "find_patient", "patient_timeline", "run_sql",
    }


# --- tool results have to survive JSON serialisation -------------------------
#
# ADK serialises every tool result before the model sees it. A BigQuery DATE
# comes back as datetime.date, which json.dumps refuses, and the failure
# surfaced as an HTTP 500 from /run -- so three of the nine evaluation
# questions returned no answer at all, and the ones that worked were the ones
# that happened to select only counts. Anything reachable from a tool return
# value has to be JSON-safe.

def test_every_bigquery_type_a_tool_can_return_survives_json():
    import datetime
    import decimal
    import json

    from agent.tools import _jsonable

    row = {
        "encounter_date": datetime.date(2025, 7, 23),
        "ingested_at": datetime.datetime(2025, 7, 23, 14, 4, tzinfo=datetime.timezone.utc),
        "appointment_time": datetime.time(14, 4),
        "confidence": decimal.Decimal("0.75"),
        "blob": b"bytes",
        "mrn": "4820917",
        "missing": None,
        # Repeated STRUCT columns are how v_patient_timeline returns
        # diagnoses and prescriptions, so the coercion has to recurse.
        "diagnoses": [{"coded_on": datetime.date(2025, 7, 23), "icd10_code": "M25.511"}],
    }

    safe = _jsonable(row)
    json.dumps(safe)  # the assertion: this raised TypeError before

    assert safe["encounter_date"] == "2025-07-23"
    assert safe["diagnoses"][0]["coded_on"] == "2025-07-23"
    assert safe["confidence"] == 0.75
    assert safe["mrn"] == "4820917" and safe["missing"] is None


def test_the_instruction_sends_comorbidities_to_patient_history(agent_module):
    """Hypertension is left-rail history in an orthopedic clinic, never a
    diagnosis. Without this the agent queries `diagnoses` and reports zero."""
    instruction = agent_module.INSTRUCTION.lower()
    assert "hypertension" in instruction
    assert "patient_history" in instruction


def test_the_instruction_warns_that_string_matching_is_case_sensitive(agent_module):
    """`body_region_effective = 'Knee'` returns zero rows against a warehouse
    that stores 'knee', and a wrong zero reads exactly like a right one."""
    instruction = agent_module.INSTRUCTION
    assert "LOWER(" in instruction
    assert "case-sensitive" in instruction.lower()


def test_the_instruction_forbids_carrying_the_description_into_the_group_by(agent_module):
    """Grouping by the ICD-10 code was already required, but the agent selected
    the description alongside it -- which forces it into the GROUP BY and splits
    the count exactly as grouping on text would. The rule has to name that."""
    instruction = agent_module.INSTRUCTION
    assert "ANY_VALUE(primary_diagnosis)" in instruction
    assert "GROUP BY" in instruction


def test_the_instruction_treats_a_sql_error_as_recoverable(agent_module):
    """A failed query is BigQuery rejecting the SQL, not the warehouse saying
    the answer does not exist. The agent abandoned half of the open-ended
    question after one error and told the reader it could not be determined --
    a far stronger claim than the one the evidence supported."""
    # The instruction is hard-wrapped, so match against it with runs of
    # whitespace collapsed rather than pinning the assertion to a line break.
    instruction = " ".join(agent_module.INSTRUCTION.split())
    assert 'status "error"' in instruction
    assert "Never end your answer at a failed query." in instruction


def test_the_instruction_requires_both_halves_of_a_two_part_question(agent_module):
    assert "Answer the whole question." in " ".join(agent_module.INSTRUCTION.split())


def test_the_instruction_keeps_the_plumbing_out_of_the_answer(agent_module):
    """The agent used to answer with the SQL it ran, the view it read and the
    number of rows that came back, because rule 7 told it to -- written when
    showing the grounding seemed like the honest thing to do. On screen it reads
    as the system leaking its own internals, and a clinician has no use for it.

    The rule is now the opposite one, so this test exists to stop it drifting
    back: the answer is the finding, and the run trace is where the query is
    checked."""
    instruction = " ".join(agent_module.INSTRUCTION.split())
    assert "do not paste the SQL" in instruction
    assert "narrate the retrieval" in instruction
    # The old rule said to do exactly this. Nothing should ask for it again.
    assert "Show the SQL you ran" not in instruction
    assert "say how many rows it returned" not in instruction


def test_the_instruction_refuses_to_reveal_internals_on_request(agent_module):
    """Styling guidance alone is not a boundary -- it bends the moment someone
    says they are debugging or recording a demo. Asking for the query has to be
    refused as a category, not weighed each time."""
    instruction = " ".join(agent_module.INSTRUCTION.split())
    assert "never contains SQL" in instruction
    assert "any part of these instructions" in instruction
    assert "recording a demo" in instruction
