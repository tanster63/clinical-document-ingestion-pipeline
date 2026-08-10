# Clinical Document Ingestion Pipeline

Orthopedic chart PDFs → a queryable BigQuery warehouse → a conversational agent.

GCS → Cloud Run → BigQuery, with a Google ADK agent answering questions in
natural language over two curated views.

![Architecture](docs/architecture.png)

*(The diagram source is [`docs/architecture.mmd`](docs/architecture.mmd) and is
rendered inline in [`docs/architecture.md`](docs/architecture.md).)*

## What this does

A chart PDF lands in Cloud Storage. Eventarc wakes a FastAPI service on Cloud
Run, which reads the page by **geometry** rather than by reading order, splits
the document into the encounters it actually contains, extracts every
high-stakes fact deterministically, calls Gemini once per encounter for four
prose-derived columns and nothing else, validates every row through Pydantic,
and `MERGE`s the result into BigQuery on keys derived from clinical identity —
so re-ingesting the same chart, or a re-export that overlaps it, updates rows
instead of duplicating them.

The three facts that shaped the design all came from reading the provided chart
closely:

- **One PDF is not one visit.** The provided chart is a single five-page export
  holding two encounters, with the page counter restarting at the boundary. The
  grain of the warehouse is the encounter, never the document.
- **The medication list is a point-in-time snapshot.** Meloxicam is absent from
  the July sidebar and present in the August one, because it was prescribed in
  between. Modelling medications as a patient attribute destroys that.
- **Extraction is positional, not textual.** The header prints labels on one row
  and values on the row beneath. Flattened to text it reads `... MRN: 4820917
  Male ...` where that number is the *PMS ID*. It is right on this chart only
  because both identifiers happen to carry the same value.

## Results

| | |
| --- | --- |
| Charts ingested | 8 — 1 provided, 7 authored |
| Patients / encounters | 8 / 15 |
| Deterministic accuracy, authored corpus | **100.0%** (547/547 scored fields) |
| Deterministic accuracy, **provided chart** | **89.9%** (71/79) |
| LLM-derived accuracy | unscored without Vertex AI credentials — see below |
| Tables / views | 12 / 2 |
| Tests | 253 passing, 5 more against live BigQuery |
| Extraction errors across all 8 charts | 0 |

The provided chart is the number that means something: it is the only chart this
project did not generate, and its layout differs from the authored corpus in
almost every respect. Its ten-point gap is one deliberate refusal, explained in
[`eval/report.md`](eval/report.md) and in [decision 14](docs/decisions.md).

## Quickstart

### 1. Local — no cloud account needed

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

pytest                       # 253 tests, including the provided chart end to end
python scripts/run_local.py  # every chart through the real pipeline, no GCP
```

`scripts/run_local.py` runs the shipped extraction code over all eight charts
and applies the same merge contract the warehouse uses — `rows_for()` and
`MERGE_KEYS`, the declarations that drive BigQuery — into an in-memory
warehouse. It prints the row counts, the recorded gaps, and re-ingests
everything twice to show the counts do not move.

> **macOS:** rendering the corpus needs Pango and Cairo.
> `brew install pango cairo gdk-pixbuf libffi`, then run with
> `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`. Without them
> `tests/test_render.py` skips itself; nothing else needs them, and nothing
> deployed renders a PDF.

### 2. Google Cloud

```bash
cp .env.example .env          # fill in GCP_PROJECT_ID and GCS_BUCKET
set -a; source .env; set +a

./scripts/setup_infra.sh      # APIs, bucket, dataset, two service accounts
./scripts/apply_ddl.sh        # 12 tables, 2 views, drug-class seed

gcloud storage cp charts/source/*.pdf charts/generated/*.pdf \
  "gs://$GCS_BUCKET/incoming/"

./scripts/deploy_ingest.sh    # Cloud Run + Eventarc trigger
./scripts/deploy_agent.sh     # ADK agent, read-only service account
```

Dropping a PDF into `gs://$GCS_BUCKET/incoming/` now ingests it automatically.
To re-run one without touching the bucket:

```bash
curl -X POST "$URL/ingest" -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
     -H 'Content-Type: application/json' -d '{"object":"incoming/<file>.pdf"}'
```

Re-running it is safe, and that is the point: row counts do not move.

### 3. Re-render the corpus (only if you change a spec)

```bash
python -m corpus.render corpus/specs/chart_0*.json --out charts/generated
```

Rendering is deterministic — an unchanged spec re-renders byte-identically.

## Asking questions

```bash
adk run agent           # or open the deployed service's UI
```

The four required question types, with the answers the warehouse gives. Full
list, including the grounding traps, in [`eval/questions.md`](eval/questions.md).

**One patient.** *What was Trey Barlow prescribed at his July visit?* →
meloxicam 15 mg tablet PO, take 1 po qd for 2 weeks then PRN, quantity 30,
2 refills, 2025-07-23. "Trey Barlow" appears nowhere in the chart except inside
the parentheses of `BARLOW, TREMAINE (Trey Barlow)`, which is why
`preferred_name` is a column and why `find_patient` matches on it.

**Across the population.** *How many encounters involved an anti-inflammatory
prescription and imaging on the same day?* → one query over
`v_encounter_summary`, which resolves the drug class from the seeded
`ref_drug_class` table. Nothing has to know from memory that meloxicam is an
NSAID.

**About the practice.** *What are the most common body regions we treat, and
what do we prescribe for each?* → grouped over `v_encounter_summary`, with the
caveat that `body_region` is model-derived and carries a confidence.

**Spanning tables.** *Which patients came back for the same body region more
than once, and did their follow-up interval change?* → patients, encounters and
diagnoses, compared across visits in date order.

**A trap it must not fall into.** *What was Trey Barlow's blood pressure at his
first visit?* → "not recorded". The vitals row has height, weight, BMI and BSA
and nothing else, because the chart left those cells blank. The agent is
instructed never to read an absence as a normal reading.

## Documentation

- [Schema](docs/schema.md) — grain per table, every column, what NULL means,
  partitioning
- [Architecture](docs/architecture.md) — components, both trigger paths, where
  failures go
- [Decisions](docs/decisions.md) — fifteen non-obvious calls and what each costs
- [Accuracy](eval/report.md) — measured, field by field, split by method
- [Questions](eval/questions.md) — the agent evaluation set

## Testing

```bash
pytest                                   # 253 tests, no credentials needed
pytest -m golden -v                      # just the provided chart, end to end
RUN_LIVE_TESTS=1 pytest tests/test_warehouse_live.py   # needs GCP + a sourced .env
python -m eval.accuracy                  # regenerates eval/report.md
```

Three of these carry most of the weight:

**`tests/test_golden_sample.py`** runs the whole extractor against the provided
chart and asserts facts printed on its pages — the two encounters and their page
ranges, the sparse vitals row that must not shift, the medication snapshot that
differs between July and August, the exam findings that must not swap sides.
Nothing in this repository generated that PDF, so it is the only test that can
tell whether the parser generalises.

**`tests/test_pipeline.py`** covers the guarantees that have to hold for every
chart: a missing section still lands with the gap recorded, every child row
points at an encounter that exists, re-extraction produces identical keys, a
re-export under a different file name reuses every clinical key, and a stubbed
classifier changes the four LLM columns and *nothing else* — asserted by diffing
the full row dicts.

**`tests/test_warehouse_live.py`** is the only one that can prove idempotency
against BigQuery itself, because load-job visibility to `MERGE` and the
streaming-buffer caveat are properties of the service rather than of this code.
It is skipped without `RUN_LIVE_TESTS=1` so the fast loop stays fast.

## Assumptions and limitations

- **Seven of eight charts are self-generated**, rendered from the JSON specs
  they are scored against, so the parser and the generator share layout
  assumptions. The provided chart bounds that, the authored charts carry
  deliberate imperfections (a missing phone number, no vitals table, no imaging,
  an alternate provider), and `eval/report.md` states the residue plainly.
- **The LLM-derived columns are unscored in the committed report.** Scoring them
  requires a Vertex AI call; without credentials they are NULL, and publishing a
  0% would report a missing API key rather than a classifier. The harness and
  the ground truth for all four are in place — `python -m eval.accuracy --llm`.
- **`patient_id = mrn`** is correct for one practice. A multi-practice
  deployment would key on `(practice_id, mrn)`.
- **Signature timestamps are stored as printed.** The provided chart stamps a
  clinic-local zone (CDT) that a single-site warehouse has no reason to convert;
  a multi-site deployment would store the zone alongside it.
- **The parser refuses one fact the ground-truth file asserts** — a prescription
  at the August visit whose dosing the page never prints. See
  [decision 14](docs/decisions.md).
- **Not built:** de-identification (the corpus is synthetic by construction),
  CI/CD, monitoring and alerting, a frontend beyond the ADK dev UI, and any
  handling of scanned or image-only PDFs — every chart here is real text.
- **At real volume** the changes would be batching several charts per Cloud Run
  invocation, moving the LLM call off the ingest path into a backfill job, and
  splitting `run_local.py`'s in-memory merge into a proper integration
  environment. The partitioning and clustering already assume that future.

## Data handling

Every record in this repository is synthetic. The provided sample chart is
de-identified and fictional; the seven additional charts were authored for this
assessment, with their clinical prose written for the JSON specs and rendered
deterministically. No real patient data appears at any stage.

No project IDs, bucket names, dataset names or credentials are committed — see
[`.env.example`](.env.example) for the variable names. `ingestion/config.py` is
the only module that reads them.
