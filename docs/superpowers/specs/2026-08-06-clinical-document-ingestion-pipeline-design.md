# Clinical Document Ingestion Pipeline — Design

**Project:** Zion Cloud Solutions — Data Engineer take-home assessment
**Author:** Tanush Pradhan
**Date:** 2026-08-06
**Deadline:** 2026-08-13 (Thursday)
**Status:** Approved design

---

## 1. Problem

An orthopedic practice (fictional: Cumberland Orthopedics) generates a PDF chart for every
patient visit. The clinical facts inside — who the patient is, what they presented with, what
was found, what was done — are written for a clinician to read, not for a database to query.

Build a pipeline that ingests chart PDFs from Cloud Storage, extracts clinically meaningful
facts, lands them in BigQuery as queryable rows with provenance back to the source page, and
puts a conversational agent in front of the warehouse.

### Fixed constraints (from the brief)

| Constraint | Detail |
|---|---|
| Cloud platform | Google Cloud. GCS → Cloud Run → BigQuery. Non-negotiable. |
| Agent framework | Google Agent Development Kit (ADK). |
| Data | All synthetic. No real PHI at any stage. |
| Idempotency | Re-ingesting the same chart must not duplicate rows. |
| Provenance | Every extracted fact traceable to its source document page. |
| Resilience | A chart missing a section must still land, with the gap recorded, not crash the job. |
| Scope | Working prototype. No polished frontend. |

### Grading weights

| Dimension | Weight |
|---|---|
| Data modeling & schema design | 30% |
| Pipeline engineering | 25% |
| Extraction quality | 15% |
| Agent & query layer | 15% |
| Communication & craft | 15% |

Schema design and pipeline engineering are 55% combined. Design effort is allocated accordingly.

---

## 2. What the sample chart actually contains

The provided chart (`EMA_20250723T140400_0000_MRN4820917_PMS4820917_PID18442091_PatientChart_400112.pdf`)
is five pages covering patient Tremaine "Trey" Barlow, MRN 4820917. Three structural facts
drive the entire design:

**It holds two encounters, not one.** Pages 1–3 are the July 23 2025 visit; pages 4–5 are the
August 13 2025 follow-up. The page counter *resets* to "Page 1" at the boundary. That reset,
combined with the `Visit Note — <date>` header, is the encounter splitter.

**The sidebar is a point-in-time snapshot, not patient-level truth.** Meloxicam is absent from
the medication list at the July visit and present at the August one, because it was prescribed
in between. Modeling medications as a patient attribute destroys this information.

**Extraction order is positional, not logical.** `MRN:` and its value `4820917` are far apart in
PyMuPDF's text stream, and the repeating header block lands at the *end* of each page's text.
Parsing must work in coordinates, not in reading order. Additionally, MRN and PMS ID happen to
be the same value in this chart — a coincidence that will break any parser that assumes it.

Other content present: patient identifiers repeated per page, medication list, medical/surgical/
family/social history, a vitals table (mostly blank — only Ht/Wt/BMI/BSA populated), a structured
physical exam (ROM active and passive, strength, special tests, inspection, skin, stability),
X-ray interpretation with ICD-10 codes, impression and plan, prescription with full dosing sig,
follow-up interval, free-text clinical note, and an electronic signature.

The filename encodes MRN, PMS ID, and a PID — a usable cross-check signal.

---

## 3. Architecture

```
   charts/*.pdf
        │  gsutil cp
        ▼
┌────────────────────────┐
│ GCS: <proj>-charts-raw │
│   incoming/*.pdf       │
└───────────┬────────────┘
            │  object.finalized  (Eventarc)
            ▼
┌──────────────────────────────────────────────┐
│ Cloud Run: chart-ingest        (FastAPI)     │
│                                              │
│  POST /events   ← Eventarc CloudEvent        │
│  POST /ingest   ← manual {"gcs_uri": ...}    │
│                                              │
│   download → sha256 → layout parse           │
│   → encounter split → field extract          │
│   → Gemini prose pass → validate → MERGE     │
└───────────┬──────────────────────────────────┘
            │
            ▼
┌────────────────────────┐      ┌──────────────────────┐
│ BigQuery: cumberland   │◄─────│ Cloud Run: chart-    │
│  11 tables + 2 views   │      │ agent (ADK + tools)  │
└────────────────────────┘      └──────────────────────┘
```

### Decision: both trigger paths

The brief leaves the trigger open and asks for justification. We build both:

- **Eventarc on `object.finalized`** — the demo video (§6.6) explicitly requires showing a PDF
  landing in the bucket and rows appearing in BigQuery. That demands automatic firing.
- **`POST /ingest`** — backfill and re-runs without touching the bucket, which is also how the
  idempotency demonstration is driven.

One FastAPI app, two routes. The second path costs ~15 lines.

### Decision: two Cloud Run services

The ingester and the agent deploy separately. They scale differently, fail differently, and
need different IAM: the ingester needs GCS read + BigQuery write; the agent needs BigQuery read
only. Separate service accounts with narrow grants is the defensible answer on least privilege.

### Decision: source-based deploy

`gcloud run deploy --source .` builds via Cloud Build with buildpacks. No Dockerfile, no local
Docker daemon required.

---

## 4. Data model

### 4.1 Tables

| Table | Grain — one row per… |
|---|---|
| `documents` | ingested PDF (provenance + audit anchor) |
| `patients` | patient, natural key MRN |
| `encounters` | visit — **the spine** |
| `diagnoses` | diagnosis within an encounter |
| `prescriptions` | prescription *written* at an encounter |
| `medication_snapshots` | (encounter × medication) the patient was *already on* |
| `imaging_studies` | imaging study within an encounter |
| `vitals` | encounter (wide, nullable columns) |
| `exam_findings` | measurement — (encounter × body part × test) |
| `ingestion_issues` | problem detected during a run |
| `ref_drug_class` | drug → therapeutic class (seed data) |

Two curated views sit on top: `v_encounter_summary` (patient + encounter + primary diagnosis,
pre-joined) and `v_patient_timeline` (ordered visit history). **The agent queries the views, not
the raw tables.**

### 4.2 Key columns by table

**`documents`** — `document_id` (sha256 of file bytes, PK), `gcs_uri`, `file_name`, `file_bytes`,
`page_count`, `mrn_from_filename`, `pms_id_from_filename`, `ingested_at`, `ingest_run_id`,
`pipeline_version`, `parse_status` (`ok` | `partial` | `failed`).

**`patients`** — `patient_id` (= MRN), `mrn`, `pms_id`, `legal_name`, `family_name`, `given_name`,
**`preferred_name`**, `date_of_birth`, `sex`, `phone_home`, `phone_work`, `first_seen_date`,
`last_seen_date`, `source_document_id`, `ingested_at`.

**`encounters`** — `encounter_id` (deterministic hash), `patient_id`, `encounter_date`,
`encounter_seq`, `provider_name`, `provider_role`, `is_primary_provider`, `location_name`,
`chief_complaint_raw`, `body_region`, `laterality`, `visit_type`, `hpi_text`, `hpi_summary`,
`note_text`, `follow_up_interval_days`, `follow_up_raw`, `signed_by`, `signed_at`,
`source_document_id`, `source_page_start`, `source_page_end`, `llm_model`, `llm_confidence`.

> **Which columns are LLM-derived, precisely.** Exactly four columns on `encounters` come from the
> model: `body_region`, `laterality`, `visit_type`, `hpi_summary`. Every other column in every
> table is deterministically parsed. `llm_model` and `llm_confidence` describe that set of four and
> nothing else. This is stated as a rule rather than a per-column flag so the boundary is
> unambiguous when reading a row — and it is what makes the two extraction paths separately
> scoreable in §8.

**`diagnoses`** — `diagnosis_id`, `encounter_id`, `patient_id`, `icd10_code`, `icd10_description`,
`diagnosis_text`, `is_primary`, `body_region`, `laterality`, `source`(`impression`|`imaging`),
`source_document_id`, `source_page`.

`diagnoses.body_region` and `diagnoses.laterality` are deterministic, not LLM-derived: resolved
from the ICD-10 code via lookup where the code carries the information (M25.511 encodes right
shoulder), otherwise inherited from the parent encounter.

**`prescriptions`** — `prescription_id`, `encounter_id`, `patient_id`, `drug_name`, `strength`,
`strength_unit`, `dose_form`, `route`, `sig_text`, `quantity`, `quantity_unit`, `refills`,
`duration_days`, `is_prn`, `drug_class`, `action` (`new`|`modify`|`continue`),
`source_document_id`, `source_page`.

**`medication_snapshots`** — `encounter_id`, `patient_id`, `medication_name`, `route`,
`source_document_id`, `source_page`.

**`imaging_studies`** — `imaging_id`, `encounter_id`, `patient_id`, `modality`, `body_part`,
`laterality`, `performed_date`, `interpretation_text`, `impression`, `source_document_id`,
`source_page`.

**`vitals`** — `encounter_id`, `patient_id`, `taken_by`, `taken_date`, `bp_systolic`,
`bp_diastolic`, `pulse`, `respirations`, `o2_sat`, `temperature_f`, `height_in`, `weight_lbs`,
`bmi`, `bsa`, `is_patient_reported`, `source_document_id`, `source_page`.

**`exam_findings`** — `finding_id`, `encounter_id`, `patient_id`, `body_part`, `laterality`,
`finding_type` (`rom_active`|`rom_passive`|`strength`|`special_test`|`inspection`|`skin`|
`stability`), `measure_name`, `value_numeric`, `value_text`, `unit`, `source_document_id`,
`source_page`.

**`ingestion_issues`** — `issue_id`, `document_id`, `encounter_id` (nullable), `severity`
(`warn`|`error`), `issue_type` (`missing_section`|`unparsed_field`|`low_confidence`|
`validation_failed`|`identifier_mismatch`), `field_name`, `detail`, `created_at`, `ingest_run_id`.

**`ref_drug_class`** — `drug_name`, `drug_class`, `is_anti_inflammatory`. Seed data.

### 4.3 The five decisions worth defending

**1. `preferred_name` is a required column.** The chart's legal name is `BARLOW, TREMAINE
(Trey Barlow)`. The brief's own example question is *"What was Trey Barlow prescribed at his July
visit?"* — a name appearing nowhere except inside those parentheses. Storing only family/given
name fails the exact question provided. The agent's `find_patient` tool matches across legal,
given, family, and preferred names.

**2. Medications split across two tables because they are two different facts.** The sidebar list
is *what the patient is currently taking*; the plan section is *what was prescribed today*. In the
sample, meloxicam is absent from the sidebar in July and present in August. One table cannot hold
both without misrepresenting its grain. The split also makes answerable: *"was this patient
already on an NSAID when we prescribed another one?"*

**3. `drug_class` comes from a lookup table, not the LLM.** Answering *"which patients on an
anti-inflammatory…"* requires knowing meloxicam is an NSAID. A seeded `ref_drug_class` table is
deterministic, auditable, and cannot hallucinate a drug class.

**4. `ingestion_issues` is a warehouse table, not a log line.** The brief requires that a chart
missing a section still lands with the gap *recorded*. Recording in BigQuery rather than Cloud
Logging makes the gap queryable, demonstrable in the video, and answerable by the agent
(*"which charts are incomplete?"*).

**5. Idempotency via deterministic keys + MERGE, not delete-and-reinsert.**

Every ID is a hash of its business key:

```
encounter_id  = sha256(patient_id ‖ encounter_date ‖ provider_name)
diagnosis_id  = sha256(encounter_id ‖ icd10_code ‖ diagnosis_text)
prescription_id = sha256(encounter_id ‖ drug_name ‖ strength ‖ sig_text)
```

Extraction writes to a per-run staging table via a **load job**, then `MERGE` upserts into the
target on that key.

Two details are load-bearing:

- Delete-by-`source_document_id`-then-insert *appears* idempotent but is not. The sample proves one
  PDF can contain multiple encounters, so a re-export overlapping a previous document would
  duplicate at encounter grain. Merging on the natural key is correct regardless of how documents
  slice up.
- It must be a **load job, not a streaming insert.** Rows in BigQuery's streaming buffer are not
  reliably visible to `MERGE`.

Re-running therefore performs `UPDATE` with identical values; row counts do not move. This is
demonstrated live in the video, not asserted.

### 4.4 Resolved modeling choices

- **Vitals: wide.** One row per encounter, eleven nullable columns mirroring the source table,
  plus `is_patient_reported`. Matches the chart's own shape; tall adds a join for no benefit at
  eleven fixed measures. NULLs record the gap.
- **Exam findings: tall, built last.** Largest structured block in the chart and the most parsing
  work for the least rubric weight — none of the four example questions touch it. Explicit cut
  line: if the video is unrecorded by Wed Aug 12, `exam_findings` is dropped to a `raw_exam_text`
  column on `encounters` with a README note stating the tradeoff.

### 4.5 Partitioning

`encounters` partitioned by `encounter_date`, clustered by `patient_id`. At eight charts this is
irrelevant to performance; it is included because it costs nothing now and is the thing you would
otherwise retrofit. The README states this explicitly rather than implying the volume justifies it.

---

## 5. Synthetic corpus

Seven charts authored as JSON specs under `corpus/specs/`, rendered through Jinja2 + WeasyPrint
templates reproducing the EMR layout: sidebar left, body right, header band repeating per page via
CSS `position: fixed`. Rendered as real text, not scanned images.

| # | Region | Visits | Deliberate quirk |
|---|---|---|---|
| 0 | shoulder R *(provided)* | 2 | vitals mostly blank |
| 1 | knee | 3 | — |
| 2 | lumbar spine | 3 | operative note |
| 3 | hand / wrist | 1 | — |
| 4 | foot / ankle | 1 | no vitals row, no phone number |
| 5 | hip | 2 | different provider |
| 6 | elbow | 2 | no imaging section |
| 7 | cervical spine | 1 | — |

Satisfies every §5.2 requirement: seven distinct regions, two charts with 3+ visits, three
single-visit charts, one operative note, one alternate provider, two deliberate imperfections,
no shared MRNs. Fifteen encounters across eight patients.

Clinical prose is LLM-authored into the JSON specs; rendering is deterministic. The README states
this, as §5.2 requires.

### The circularity tradeoff, stated openly

Parsing PDFs generated from a template you also wrote is circular, and hiding that would read as
luck rather than judgment. Two things defuse it, and both belong in the README:

1. The provided sample chart came out of a real EMR and was not authored here. It remains the acid
   test the parser must pass unmodified.
2. The mandated imperfections mean the authored charts are not uniform either.

The benefit is that ground truth exists for seven of eight charts, so extraction accuracy is
*computed*, not estimated — which is what §7 asks for directly.

---

## 6. Ingestion

### 6.1 Modules

| Module | Responsibility |
|---|---|
| `ingestion/app.py` | FastAPI routes only — `/events`, `/ingest`, `/healthz` |
| `extract/layout.py` | PyMuPDF `get_text("dict")`; coordinate-based column/band split |
| `extract/encounters.py` | Encounter splitting → page ranges |
| `extract/sections.py` | Section heading detection within body |
| `extract/fields/` | Per-domain parsers: identifiers, vitals, prescriptions, ICD-10, follow-up |
| `extract/llm.py` | Single Gemini structured-output call per encounter |
| `models.py` | Pydantic contracts, validated before write |
| `warehouse.py` | Staging load job + MERGE |
| `config.py` | Env-driven configuration; no literals in code |

The sidebar/body boundary is **derived from page geometry, not a hardcoded pixel**, so it survives
a chart rendered at a different page size.

ICD-10 pattern: `[A-Z]\d{2}(\.\d{1,4})?`. Follow-up intervals normalize to days
(`"Follow up in 3 weeks"` → 21).

### 6.2 Filename cross-check

The filename encodes MRN and PMS ID, which cross-checks the header block. A mismatch writes an
`identifier_mismatch` issue row rather than being silently trusted. The brief points at this
directly: *"each of those is a design decision the source system made, and each one is a signal
you can use."*

### 6.3 LLM boundary

One structured-output call per encounter, temperature 0, returning exactly: `body_region`,
`laterality`, `visit_type`, `hpi_summary`, `confidence`. **An LLM value never overwrites a
deterministically-parsed field.** Every LLM-derived column carries `extraction_method='llm'` so
the two paths are separable in the accuracy report.

Deterministic parsing owns everything where being wrong is unacceptable: identifiers, dates,
ICD-10 codes, prescriptions with sig/quantity/refills, vitals, provider, follow-up interval.

### 6.4 Error handling

| Failure | Behaviour |
|---|---|
| Missing section | `warn` issue row; encounter still lands |
| Field validation failure | `error` issue row; offending field nulled; rest lands |
| One bad encounter | Guarded independently; other encounters in the document unaffected |
| Unparseable file | `documents` row with `parse_status='failed'` + issue row |

**Always return 2xx to Eventarc.** A non-2xx triggers redelivery, so a deterministically failing
chart would retry forever against a live billing account. Failures are acked and recorded, never
re-thrown.

---

## 7. Query agent

An ADK `LlmAgent` over Gemini exposing four tools:

| Tool | Purpose |
|---|---|
| `get_schema()` | Table and column descriptions |
| `find_patient(name_or_mrn)` | Matches legal, given, family, **and preferred** name |
| `patient_timeline(patient_id)` | Pre-joined visit history |
| `run_sql(sql)` | **Guarded**: SELECT-only, dataset-scoped, LIMIT injected, dry-run byte cap |

Coverage of the four required question types:

| Question type | Path |
|---|---|
| Fact about one patient | `find_patient` → `patient_timeline` |
| Aggregate across population | `run_sql` over `v_encounter_summary.body_region` |
| Open question about the practice | `run_sql` — diagnosis frequency joined to treatments |
| Spanning multiple tables | `run_sql` — `prescriptions` ⋈ `imaging_studies` on patient + date |

The last question (*"patients on an anti-inflammatory who had imaging on the same day"*) is why
`ref_drug_class.is_anti_inflammatory` and `imaging_studies.performed_date` exist as columns.

**Grounding.** The system prompt states that answers come only from returned rows, that counts are
cited, and that empty results are reported as "not in the data" rather than filled in from what the
model knows about orthopedics. The agent queries BigQuery only; it never re-reads PDFs.

Deployed via `adk deploy cloud_run`.

---

## 8. Testing and measured error rate

**Unit tests** — pytest over text fixtures for each field parser.

**Golden test** — full extraction against the provided sample chart, the one PDF not authored
here. This is the honest measure of whether the parser generalizes.

**Idempotency test** — ingest twice, assert identical row counts across all tables.

**`eval/accuracy.py`** — diffs extracted rows against `corpus/specs/*.json` field by field, writes
`eval/report.md` with per-field accuracy, split by `extraction_method` so deterministic and LLM
paths are scored separately. The sample chart is hand-labelled once into `sample_truth.json` so it
counts toward the number.

This produces the sentence §7 of the brief asks for: *"Field-level extraction accuracy is N%, and
here are the fields that miss."*

---

## 9. Repository layout

```
zcs-clinical-pipeline/
├── README.md                  ← the graded artifact
├── docs/  architecture.md · schema.md · decisions.md
├── charts/  source/ (provided) · generated/ (authored)
├── corpus/  specs/*.json (ground truth) · render.py · templates/
├── ingestion/
│   ├── app.py · models.py · warehouse.py · config.py
│   └── extract/  layout · sections · encounters · fields/ · llm
├── agent/  agent.py · tools.py
├── sql/  ddl/ · merge/
├── eval/  accuracy.py · report.md
└── tests/
```

`config.py` reads project ID, dataset, bucket, and model name from environment variables, so the
local run and the deployed service are the same code path with different env. This is a direct
response to the criterion *"is configuration separated from code?"*

`<proj>` throughout this document is the GCP project ID, which is bound on Fri Aug 7 during
infrastructure setup and thereafter lives only in `.env` / Cloud Run env vars. Concrete values —
project ID, bucket name, dataset name, region, Gemini model — appear in no source file.

---

## 10. Schedule

Today is Thursday 2026-08-06. Deadline Thursday 2026-08-13. Available time is roughly 35 hours,
weighted toward the weekend.

| Day | Work |
|---|---|
| **Fri Aug 7** | gcloud install, APIs, bucket, dataset, service accounts · repo skeleton · DDL · one chart rendering end-to-end |
| **Sat Aug 8** | Corpus complete — 7 charts + ground-truth JSON |
| **Sun Aug 9** | Extraction core — layout, encounter split, identifiers, diagnoses, prescriptions, imaging |
| **Mon Aug 10** | MERGE + idempotency + issues table · full local end-to-end |
| **Tue Aug 11** | Cloud Run deploy · Eventarc · agent + tools |
| **Wed Aug 12** | Eval number · README · architecture diagram · **record the video** |
| **Thu Aug 13** | Buffer, retakes, `exam_findings` if free, submit |

The two heaviest build days (corpus, extraction) land on the weekend, when hours are longest.

The video is recorded Wednesday, not on the due date. The buffer day also absorbs the first Cloud
Run IAM problem, which should be expected to cost roughly two hours.

---

## 11. Deliverables checklist

| § | Deliverable | Where |
|---|---|---|
| 6.1 | 8 chart PDFs in GCS + committed | `charts/`, `gs://<proj>-charts-raw/incoming/` |
| 6.2 | Cloud Run ingestion service | `ingestion/` |
| 6.3 | BigQuery dataset + documented schema | `sql/ddl/`, `docs/schema.md` |
| 6.4 | ADK agent answering 4 question types | `agent/` |
| 6.5 | GitHub repo, README, architecture diagram, clean history | repo root, `docs/` |
| 6.6 | Demo video, 5–8 min | recorded Wed Aug 12 |

Demo video must show: a PDF entering the bucket and rows appearing in BigQuery; the agent
answering at least three questions including one spanning multiple tables; and a brief explanation
of the schema and its reasoning.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Cloud Run / Eventarc IAM friction (ADK and Cloud Run both new) | Buffer day; source-based deploy avoids Docker entirely |
| WeasyPrint system dependencies on macOS | Verified Fri Aug 7 as part of the "one chart rendering" milestone; Playwright headless Chromium is the fallback |
| Parser overfits to self-authored charts | Sample chart is a mandatory golden test; accuracy reported separately for it |
| `exam_findings` consumes video time | Pre-agreed cut line, documented as a tradeoff |
| Eventarc retry storm on a failing chart | Always return 2xx; failures recorded, never re-thrown |

---

## 13. Explicitly out of scope

Frontend beyond the ADK dev UI. Real PHI of any kind. Production concerns — CI/CD, monitoring,
alerting, autoscaling policy, disaster recovery. De-identification tooling (corpus is synthetic by
construction). Multi-practice tenancy: `patient_id = MRN` is correct for a single practice, and the
README notes that a real deployment would key on `(practice_id, mrn)`.
