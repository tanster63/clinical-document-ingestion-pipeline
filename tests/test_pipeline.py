"""Whole-document behaviour: resilience, idempotency, and the LLM boundary.

These run over the rendered corpus as well as the provided chart, because the
guarantees they check — a missing section still lands, re-ingesting changes
nothing, the LLM never touches a parsed column — have to hold for every chart,
not just the one that was easiest to write for.
"""

from datetime import date

import pytest

from corpus.spec_model import load_spec
from ingestion.extract.pipeline import extract_document
from ingestion.warehouse import rows_for

SAMPLE_NAME = ("EMA_20250723T140400_0000_MRN4820917_PMS4820917"
               "_PID18442091_PatientChart_400112.pdf")


@pytest.fixture(scope="module")
def corpus(generated_pdfs, module_cfg):
    return [extract_document(p.read_bytes(), file_name=p.name, cfg=module_cfg)
            for p in generated_pdfs]


def test_the_whole_corpus_extracts_without_a_single_error(corpus):
    errors = [(d.document.file_name, i.issue_type, i.detail)
              for d in corpus for i in d.issues if i.severity == "error"]
    assert errors == []
    assert all(d.document.parse_status == "ok" for d in corpus)


def test_the_corpus_lands_the_expected_grain(corpus):
    assert len({d.patient.mrn for d in corpus}) == 7
    assert sum(len(d.encounters) for d in corpus) == 13


def test_every_chart_matches_the_encounter_count_it_was_authored_with(corpus, generated_pdfs):
    for doc, path in zip(corpus, generated_pdfs):
        spec = load_spec(f"corpus/specs/{_spec_stem(path.name)}.json")
        assert len(doc.encounters) == len(spec.encounters), path.name


def _spec_stem(file_name: str) -> str:
    import json
    from pathlib import Path
    for spec_path in sorted(Path("corpus/specs").glob("chart_*.json")):
        if json.loads(spec_path.read_text())["file_name"] == file_name:
            return spec_path.stem
    raise AssertionError(f"no spec renders {file_name}")


def test_a_chart_with_no_imaging_section_still_lands_and_records_the_gap(corpus):
    """chart_06 was authored without imaging on purpose."""
    without = [d for d in corpus if not d.imaging]
    assert without, "expected at least one chart with no imaging"
    for doc in without:
        assert doc.encounters
        assert any(i.field_name == "imaging" and i.issue_type == "missing_section"
                   for i in doc.issues)


def test_a_chart_with_no_vitals_row_still_lands_and_records_the_gap(corpus):
    """chart_04 was authored without a vitals table."""
    without = [d for d in corpus if not d.vitals]
    assert without
    for doc in without:
        assert doc.encounters
        assert any(i.field_name == "vitals" for i in doc.issues)


def test_a_missing_phone_number_is_not_an_error(corpus):
    missing = [d for d in corpus if d.patient.phone_home is None]
    assert missing
    assert all(not [i for i in d.issues if i.severity == "error"] for d in missing)


def test_every_child_row_points_at_an_encounter_that_exists(corpus):
    for doc in corpus:
        known = {e.encounter_id for e in doc.encounters}
        children = (doc.vitals + doc.diagnoses + doc.prescriptions
                    + doc.medications + doc.imaging + doc.exam_findings)
        assert {row.encounter_id for row in children} <= known
        assert all(row.patient_id == doc.patient.patient_id for row in children)


def test_every_row_carries_provenance_back_to_its_document(corpus):
    for doc in corpus:
        document_id = doc.document.document_id
        rows = (doc.encounters + doc.vitals + doc.diagnoses + doc.prescriptions
                + doc.medications + doc.imaging + doc.exam_findings)
        assert all(row.source_document_id == document_id for row in rows)
        assert all(issue.document_id == document_id for issue in doc.issues)


def test_llm_columns_are_the_only_thing_a_model_touches(corpus, module_cfg,
                                                        generated_pdfs):
    """With a stubbed classifier the four prose columns fill in and nothing
    else in the document changes by so much as a byte."""
    import json
    from types import SimpleNamespace

    payload = {"body_region": "knee", "laterality": "left", "visit_type": "new",
               "hpi_summary": "stub", "confidence": 0.9}
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kw: SimpleNamespace(text=json.dumps(payload))
    ))
    path = generated_pdfs[0]
    without = extract_document(path.read_bytes(), file_name=path.name, cfg=module_cfg)
    with_llm = extract_document(path.read_bytes(), file_name=path.name, cfg=module_cfg,
                                llm_client=client)

    llm_columns = {"body_region", "laterality", "visit_type", "hpi_summary",
                   "llm_model", "llm_confidence"}
    for plain, classified in zip(without.encounters, with_llm.encounters):
        before, after = plain.to_row(), classified.to_row()
        assert {k for k in before if before[k] != after[k]} <= llm_columns
        assert after["body_region"] == "knee"
        assert after["llm_model"] == module_cfg.gemini_model

    for table in ("diagnoses", "prescriptions", "vitals", "imaging_studies",
                  "medication_snapshots"):
        assert rows_for(without).get(table) == rows_for(with_llm).get(table)


def test_re_extracting_the_same_bytes_produces_identical_keys(sample_pdf_bytes, module_cfg):
    """Idempotency starts here: if the keys move between runs, no MERGE can
    save it."""
    first = extract_document(sample_pdf_bytes, file_name=SAMPLE_NAME, cfg=module_cfg)
    second = extract_document(sample_pdf_bytes, file_name=SAMPLE_NAME, cfg=module_cfg)
    for table, rows in rows_for(first).items():
        other = rows_for(second)[table]
        assert _keys(table, rows) == _keys(table, other)


def test_a_re_export_under_a_different_name_reuses_every_clinical_key(
        sample_pdf_bytes, module_cfg):
    """The same visits exported into a differently-named file must merge onto
    the same encounter rows, not duplicate them. Only the document row differs,
    and only because its identity is the file's own bytes."""
    first = extract_document(sample_pdf_bytes, file_name="export-a.pdf", cfg=module_cfg)
    second = extract_document(sample_pdf_bytes, file_name="export-b.pdf", cfg=module_cfg)

    assert [e.encounter_id for e in first.encounters] == \
        [e.encounter_id for e in second.encounters]
    assert first.patient.patient_id == second.patient.patient_id
    assert {d.diagnosis_id for d in first.diagnoses} == \
        {d.diagnosis_id for d in second.diagnoses}
    assert {r.prescription_id for r in first.prescriptions} == \
        {r.prescription_id for r in second.prescriptions}
    assert first.document.document_id == second.document.document_id


def test_the_encounter_key_moves_when_the_visit_does(sample_pdf_bytes, module_cfg):
    doc = extract_document(sample_pdf_bytes, file_name=SAMPLE_NAME, cfg=module_cfg)
    july, august = doc.encounters
    assert july.encounter_id != august.encounter_id
    assert july.encounter_date == date(2025, 7, 23)


def _keys(table: str, rows: list[dict]) -> set:
    from ingestion.warehouse import MERGE_KEYS
    return {tuple(row[column] for column in MERGE_KEYS[table]) for row in rows}
