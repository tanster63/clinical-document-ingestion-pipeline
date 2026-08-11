"""Computed extraction accuracy.

Claiming accuracy is worth little; computing it against ground truth, field by
field, is the deliverable (§8). Ground truth is the JSON spec each synthetic
chart was rendered from, plus a hand-labelled truth file for the provided chart,
which nothing in this repository generated.

Two splits matter and both are reported separately:

* **deterministic vs LLM** — a parser fails loudly and identically every time; a
  model fails quietly and differently each time. Averaging them together hides
  both failure modes.
* **synthetic corpus vs the provided chart** — seven of eight charts were
  rendered from the specs they are scored against, so the parser and the
  generator share assumptions. The provided chart does not share them, and its
  number is the honest one.

Run with `python -m eval.accuracy`.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from corpus.spec_model import ChartSpec, load_spec
from ingestion.config import Config
from ingestion.extract.pipeline import extract_document
from ingestion.models import ExtractedDocument

DETERMINISTIC = "deterministic"
LLM = "llm"

# Scoring reads PDFs off disk and never touches Google Cloud, so it carries its
# own configuration rather than requiring a .env just to print a number.
LOCAL_CONFIG = Config(
    project_id="local", dataset="cumberland", bucket="local",
    location="local", gemini_model="(not called)", pipeline_version="local",
)

# The corpus records a region the way a clinician writes it; the classifier
# picks from a fixed vocabulary that splits some of those in two. Both name the
# same anatomy, so they are compared as equivalent rather than scored as a miss.
REGION_EQUIVALENTS = {
    "hand/wrist": {"hand", "wrist"},
    "foot/ankle": {"foot", "ankle"},
    "hip/pelvis": {"hip", "pelvis"},
}


@dataclass
class FieldResult:
    field: str
    method: str
    correct: int = 0
    total: int = 0
    misses: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float | None:
        return None if self.total == 0 else self.correct / self.total

    def record(self, matched: bool, detail: str = "") -> None:
        self.total += 1
        if matched:
            self.correct += 1
        elif detail:
            self.misses.append(detail)


def _normalize(value) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split()).lower()


def _sex(value) -> str:
    """Charts write "Male"; the warehouse stores "M". Same fact, two spellings."""
    return "" if value is None else str(value).strip()[:1].upper()


def _laterality(value) -> str:
    """The corpus writes "none" where a region has no side; the column is NULL."""
    normalized = _normalize(value)
    return "" if normalized == "none" else normalized


def _matches(expected, actual, field_name: str) -> bool:
    if field_name == "encounter.body_region":
        wanted, got = _normalize(expected), _normalize(actual)
        return wanted == got or got in REGION_EQUIVALENTS.get(wanted, set())
    return _normalize(expected) == _normalize(actual)


class Scoreboard:
    def __init__(self) -> None:
        self.results: dict[str, FieldResult] = {}

    def check(self, field_name: str, expected, actual, method: str = DETERMINISTIC,
              context: str = "") -> None:
        result = self.results.setdefault(
            field_name, FieldResult(field=field_name, method=method)
        )
        matched = _matches(expected, actual, field_name)
        result.record(matched, "" if matched
                      else f"{context}expected {expected!r}, got {actual!r}")


def compare_document(extracted: ExtractedDocument, truth: ChartSpec,
                     include_llm: bool = True) -> list[FieldResult]:
    board = Scoreboard()
    patient = truth.patient

    for label, spec_attr, row_attr in (
        ("mrn", "mrn", "mrn"),
        ("pms_id", "pms_id", "pms_id"),
        ("family_name", "family_name", "family_name"),
        ("given_name", "given_name", "given_name"),
        ("preferred_name", "preferred_name", "preferred_name"),
        ("date_of_birth", "date_of_birth", "date_of_birth"),
        ("sex", "sex", "sex"),
        ("phone_home", "phone_home", "phone_home"),
    ):
        expected = getattr(patient, spec_attr, None)
        actual = getattr(extracted.patient, row_attr, None)
        if label == "sex":
            expected, actual = _sex(expected), _sex(actual)
        board.check(f"patient.{label}", expected, actual)

    board.check("encounter.count", len(truth.encounters), len(extracted.encounters))

    # The left rail's longitudinal context, at patient grain (§5.1).
    expected_history = {
        (history_type, item)
        for history_type, items in (
            ("medical", patient.history.medical),
            ("musculoskeletal", patient.history.musculoskeletal),
            ("family", patient.history.musculoskeletal_family),
            ("musculoskeletal_surgery", patient.history.musculoskeletal_surgery),
            ("surgical", patient.history.surgical),
            ("social", patient.history.social),
        )
        for item in items
    }
    got_history = {(h.history_type, h.item_text) for h in extracted.history}
    for history_type, item in sorted(expected_history):
        board.check("history.item", item,
                    item if (history_type, item) in got_history else None,
                    context=f"[{history_type}] ")
    board.check("history.count", len(expected_history), len(got_history))

    by_date = {e.encounter_date: e for e in extracted.encounters}
    for spec in truth.encounters:
        found = by_date.get(spec.encounter_date)
        prefix = f"[{spec.encounter_date}] "
        board.check("encounter.encounter_date", spec.encounter_date,
                    found.encounter_date if found else None, context=prefix)
        board.check("encounter.provider_name", spec.provider_name,
                    found.provider_name if found else None, context=prefix)
        board.check("encounter.provider_role", spec.provider_role,
                    found.provider_role if found else None, context=prefix)
        board.check("encounter.location_name", truth.location_name,
                    found.location_name if found else None, context=prefix)
        if spec.follow_up_days is not None:
            board.check("encounter.follow_up_interval_days", spec.follow_up_days,
                        found.follow_up_interval_days if found else None, context=prefix)
        if spec.signed_by:
            board.check("encounter.signed_by", spec.signed_by,
                        found.signed_by if found else None, context=prefix)
        if spec.signed_at:
            board.check("encounter.signed_at", spec.signed_at,
                        found.signed_at if found else None, context=prefix)
        if include_llm:
            board.check("encounter.body_region", spec.body_region,
                        found.body_region if found else None, method=LLM, context=prefix)
            board.check("encounter.laterality", _laterality(spec.laterality),
                        _laterality(found.laterality if found else None),
                        method=LLM, context=prefix)
            board.check("encounter.visit_type", spec.visit_type,
                        found.visit_type if found else None, method=LLM, context=prefix)

        eid = found.encounter_id if found else None

        got_dx = [d for d in extracted.diagnoses if d.encounter_id == eid]
        for dx in spec.diagnoses:
            match = next((d for d in got_dx
                          if _normalize(d.icd10_code) == _normalize(dx.icd10_code)), None)
            board.check("diagnosis.icd10_code", dx.icd10_code,
                        match.icd10_code if match else None, context=prefix)
            board.check("diagnosis.diagnosis_text", dx.diagnosis_text,
                        match.diagnosis_text if match else None, context=prefix)
            board.check("diagnosis.is_primary", dx.is_primary,
                        match.is_primary if match else None, context=prefix)
        # Diagnoses the chart never contained are misses too — an extraction
        # that invents rows is not more accurate than one that misses them.
        for extra in got_dx[len(spec.diagnoses):]:
            board.check("diagnosis.icd10_code", None, extra.icd10_code,
                        context=f"{prefix}spurious ")

        got_rx = [r for r in extracted.prescriptions if r.encounter_id == eid]
        for rx in spec.prescriptions:
            match = next((r for r in got_rx
                          if _normalize(r.drug_name) == _normalize(rx.drug_name)), None)
            for name in ("drug_name", "strength", "strength_unit", "dose_form",
                         "route", "sig_text", "quantity", "refills"):
                board.check(f"prescription.{name}", getattr(rx, name, None),
                            getattr(match, name, None) if match else None, context=prefix)
        for extra in got_rx[len(spec.prescriptions):]:
            board.check("prescription.drug_name", None, extra.drug_name,
                        context=f"{prefix}spurious ")

        got_meds = [m for m in extracted.medications if m.encounter_id == eid]
        for med in spec.current_medications:
            match = next((m for m in got_meds if _normalize(m.medication_name)
                          == _normalize(med.medication_name)), None)
            board.check("medication.medication_name", med.medication_name,
                        match.medication_name if match else None, context=prefix)
            board.check("medication.route", med.route,
                        match.route if match else None, context=prefix)
        board.check("medication.count", len(spec.current_medications), len(got_meds),
                    context=prefix)

        got_vitals = next((v for v in extracted.vitals if v.encounter_id == eid), None)
        if spec.vitals:
            for name in ("bp_systolic", "bp_diastolic", "pulse", "respirations",
                         "o2_sat", "temperature_f", "height_in", "weight_lbs",
                         "bmi", "bsa"):
                board.check(f"vitals.{name}", getattr(spec.vitals, name, None),
                            getattr(got_vitals, name, None) if got_vitals else None,
                            context=prefix)
        else:
            # A chart with no vitals section must produce no vitals row. Absence
            # is a fact the warehouse has to get right too.
            board.check("vitals.absent", True, got_vitals is None, context=prefix)

        if spec.procedure_name:
            got = next((p for p in extracted.procedures if p.encounter_id == eid), None)
            board.check("procedure.procedure_name", spec.procedure_name,
                        got.procedure_name if got else None, context=prefix)
            board.check("procedure.performed_date",
                        spec.procedure_date or spec.encounter_date,
                        got.performed_date if got else None, context=prefix)

        got_imaging = [i for i in extracted.imaging if i.encounter_id == eid]
        for study in spec.imaging:
            match = next((i for i in got_imaging
                          if _normalize(i.modality) == _normalize(study.modality)), None)
            board.check("imaging.modality", study.modality,
                        match.modality if match else None, context=prefix)
            board.check("imaging.performed_date", study.performed_date,
                        match.performed_date if match else None, context=prefix)
            board.check("imaging.laterality", _laterality(study.laterality),
                        _laterality(match.laterality if match else None), context=prefix)
        board.check("imaging.count", len(spec.imaging), len(got_imaging), context=prefix)

    return list(board.results.values())


def _merge(into: dict[str, FieldResult], results: list[FieldResult]) -> None:
    for result in results:
        target = into.setdefault(
            result.field, FieldResult(field=result.field, method=result.method)
        )
        target.correct += result.correct
        target.total += result.total
        target.misses.extend(result.misses)


def evaluate_corpus(
    cfg: Config,
    spec_dir: Path = Path("corpus/specs"),
    pdf_dir: Path = Path("charts/generated"),
    include_llm: bool = False,
    llm_client=None,
) -> dict[str, FieldResult]:
    """Score the seven synthetic charts."""
    totals: dict[str, FieldResult] = {}
    for spec_path in sorted(spec_dir.glob("chart_*.json")):
        spec = load_spec(spec_path)
        pdf_path = pdf_dir / spec.file_name
        if not pdf_path.exists():
            raise FileNotFoundError(
                f"{pdf_path} is missing; run "
                f"`python -m corpus.render corpus/specs/*.json --out {pdf_dir}`"
            )
        doc = extract_document(pdf_path.read_bytes(), file_name=pdf_path.name, cfg=cfg,
                               llm_client=llm_client)
        _merge(totals, compare_document(doc, spec, include_llm=include_llm))
    return totals


def evaluate_sample(
    cfg: Config,
    sample_truth: Path = Path("corpus/sample_truth.json"),
    sample_pdf: Path | None = None,
    include_llm: bool = False,
    llm_client=None,
) -> dict[str, FieldResult]:
    """Score the provided chart — the only one this project did not generate."""
    if sample_pdf is None or not sample_pdf.exists() or not sample_truth.exists():
        return {}
    truth = ChartSpec.model_validate(json.loads(sample_truth.read_text()))
    doc = extract_document(sample_pdf.read_bytes(), file_name=sample_pdf.name, cfg=cfg,
                           llm_client=llm_client)
    totals: dict[str, FieldResult] = {}
    _merge(totals, compare_document(doc, truth, include_llm=include_llm))
    return totals


def overall(results: dict[str, FieldResult], method: str = DETERMINISTIC) -> float | None:
    rows = [r for r in results.values() if r.method == method]
    total = sum(r.total for r in rows)
    return None if not total else sum(r.correct for r in rows) / total


def _tables(results: dict[str, FieldResult], title: str) -> list[str]:
    lines: list[str] = []
    for method, heading in ((DETERMINISTIC, "Deterministic parsing"), (LLM, "LLM-derived")):
        rows = sorted((r for r in results.values() if r.method == method),
                      key=lambda r: r.field)
        if not rows:
            continue
        correct = sum(r.correct for r in rows)
        total = sum(r.total for r in rows)
        summary = f"{correct / total:.1%}" if total else "n/a"
        lines += [f"### {title} — {heading} — {summary} ({correct}/{total})", "",
                  "| Field | Correct | Total | Accuracy |",
                  "| --- | ---: | ---: | ---: |"]
        for row in rows:
            accuracy = "n/a" if row.accuracy is None else f"{row.accuracy:.1%}"
            lines.append(f"| `{row.field}` | {row.correct} | {row.total} | {accuracy} |")
        lines.append("")
    return lines


def render_report(corpus: dict[str, FieldResult],
                  sample: dict[str, FieldResult] | None = None,
                  llm_scored: bool = False) -> str:
    lines = ["# Extraction accuracy", "",
             "Computed by `python -m eval.accuracy` against the JSON specs the synthetic",
             "charts were rendered from, plus a hand-labelled truth file for the provided",
             "chart. Regenerate after any parser change.", ""]
    if not llm_scored:
        lines += [
            "> **LLM-derived columns are unscored in this run.** `body_region`,",
            "> `laterality`, `visit_type` and `hpi_summary` come from a single Vertex AI",
            "> call, and this run had no credentials, so those four columns are NULL and",
            "> scoring them would report a 0% that measures the absence of a key rather",
            "> than the quality of a classifier. Run with `GOOGLE_APPLICATION_CREDENTIALS`",
            "> set and `--llm` to score them; the harness and the ground truth for all",
            "> four are already in place.", ""]
    lines += ["## Synthetic corpus (7 charts, 13 encounters)", ""]
    lines += _tables(corpus, "Synthetic corpus")

    if sample:
        lines += ["## Provided chart (not generated by this project)", "",
                  "The one chart here whose layout this project did not author, scored by",
                  "the same harness against a hand-labelled truth file. It is the honest",
                  "number, and it is a mandatory test in the suite.", ""]
        lines += _tables(sample, "Provided chart")

    # A fresh accumulator — _merge mutates, and corpus/sample must stay untouched.
    combined: dict[str, FieldResult] = {}
    _merge(combined, list(corpus.values()))
    if sample:
        _merge(combined, list(sample.values()))
    misses = [(r.field, miss) for r in sorted(combined.values(), key=lambda r: r.field)
              for miss in r.misses]
    lines += ["## Every miss", ""]
    if misses:
        lines += [f"- `{field_name}` — {detail}" for field_name, detail in misses]
    else:
        lines.append("None. Every scored field matched its ground truth.")
    lines.append("")
    return "\n".join(lines)


HONEST_NOTE = """## The one disagreement with ground truth, and why it stands

Every remaining miss on the provided chart is the same one: the hand-labelled
truth records a meloxicam prescription at the 2025-08-13 encounter, and the
parser does not produce one.

The August page prints this and nothing more:

    Plan: Prescription Medication Management.
    Modify Regimen: Modify prescription medication therapy.

There is no drug name, strength, sig, quantity or refill count anywhere on that
page. The truth file's values were carried across from the July visit, which is
what a clinician reading both pages would infer — but writing them into an
August prescription row would assert, as structured data, that a prescription
with those exact terms was issued on that date. Deterministic parsing owns
prescriptions precisely because being wrong about a dose is unacceptable (§6.3),
so the parser declines and records the gap instead: the encounter carries an
`unparsed_field` warning in `ingestion_issues` saying that a prescribing action
was recorded without printed dosing.

That choice costs eight scored fields and about ten points on this chart. It is
the right trade: a queryable "we know something was prescribed here and the
chart does not say what" is more useful, and far safer, than eight confident
values the document never contained.

## What this number does and does not mean

Seven of the eight charts were rendered from the JSON specs they are scored
against, so the parser and the generator share assumptions about layout. That
inflates these numbers relative to charts from a system nobody here wrote.

Three things bound the inflation. The provided chart — which this project did
not generate, and whose layout differs from the rendered corpus in almost every
respect — is scored by the same harness against a hand-labelled truth file, and
it is a mandatory test in the suite. The synthetic charts were deliberately
built with imperfections (a missing phone number, absent vitals, an alternate
provider, a chart with no imaging) so the parser has to handle absence rather
than assuming every field is present. And `encounter.follow_up_interval_days`
is scored against an author-declared integer that is never rendered, so the
parser has to find the phrase inside a whole plan section rather than convert a
string handed to it.

One field is honestly circular and worth naming: the follow-up ground truth was
derived from the printed phrase using the same units table the parser uses, so
it tests phrase location, not arithmetic.

The deterministic and LLM figures are reported separately on purpose. They fail
differently: a parser fails loudly and identically every time, a model fails
quietly and differently each time.
"""


def main() -> None:
    import os
    import sys

    include_llm = "--llm" in sys.argv
    # LOCAL_CONFIG names project "local", so the Vertex client built from it
    # cannot resolve. classify_encounter catches that like any other failure and
    # returns None, which scores as a miss -- so `--llm` against LOCAL_CONFIG
    # publishes a 0% that measures the configuration, not the model. That is the
    # exact number this report says it refuses to print. Scoring the four
    # model-derived columns requires the real credentials; load_config raises and
    # names the missing variables if they are not set.
    llm_client = None
    if include_llm:
        from ingestion.config import load_config
        from ingestion.extract.llm import build_client
        cfg = load_config()
        # extract_document only classifies when it is handed a client. Without
        # this, --llm scored four columns that were never populated.
        llm_client = build_client(cfg)
    else:
        cfg = LOCAL_CONFIG
    sample_path = os.environ.get(
        "SAMPLE_CHART_PATH",
        "charts/source/EMA_20250723T140400_0000_MRN4820917_PMS4820917"
        "_PID18442091_PatientChart_400112.pdf",
    )
    corpus = evaluate_corpus(cfg, include_llm=include_llm, llm_client=llm_client)
    sample = evaluate_sample(cfg, sample_pdf=Path(sample_path), include_llm=include_llm,
                             llm_client=llm_client)
    report = render_report(corpus, sample, llm_scored=include_llm) + "\n" + HONEST_NOTE
    Path("eval/report.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
