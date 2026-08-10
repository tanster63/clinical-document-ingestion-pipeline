# Decisions

Every non-obvious call, in the same shape: **what was decided, what the
alternative was, why this one, and what it costs.** Where a decision was
revised during implementation, that is stated rather than tidied away.

---

## 1. The encounter is the grain, not the document

**Alternative:** one row per chart, with visits nested or flattened into it.

**Why:** the provided chart is one five-page PDF containing two encounters — a
July visit and an August follow-up — with the page counter restarting at the
boundary. Document-grain would have collapsed them into one row and destroyed
the only interesting thing in the file, which is what changed between the two.

Encounter-grain also survives the inverse case, which is the one that bites
later: a re-export covering both of those visits plus a third merges onto the
two rows that already exist and inserts one.

**Costs:** every child table needs an `encounter_id`, and splitting encounters
correctly becomes load-bearing rather than incidental — so it has its own
module and its own tests.

---

## 2. Idempotency by MERGE on natural keys, never delete-and-reinsert

**Alternative:** delete every row with this `source_document_id`, then insert.

**Why:** that *appears* idempotent and is not. One PDF can hold several
encounters, so a re-export overlapping a previous document would delete rows the
new document does not fully replace, or duplicate at encounter grain when the
slicing differs. Merging on clinical identity is correct however the documents
happen to be cut:

```
patient_id      = mrn
encounter_id    = sha256(patient_id ‖ encounter_date ‖ provider_name)
diagnosis_id    = sha256(encounter_id ‖ icd10_code ‖ diagnosis_text)
prescription_id = sha256(encounter_id ‖ drug_name ‖ strength ‖ sig_text)
document_id     = sha256(file bytes)
```

Two implementation details are load-bearing:

- **A load job, not a streaming insert.** Rows sitting in BigQuery's streaming
  buffer are not reliably visible to `MERGE`, so the same chart ingested twice
  would sometimes duplicate. It would fail only under load and only
  occasionally, which is the worst way to discover it.
- **A separator between key parts.** `sha256_key` joins on `\x1f`, so `("ab",
  "c")` and `("a", "bc")` cannot collide. Without it, two different
  prescriptions can quietly become one row.

**Costs:** a staging table per table per run, and one extra query to drop it.
Staging tables also carry a 6-hour expiry so a crashed run cannot litter the
dataset.

---

## 3. Medications are split across two tables because they are two facts

**Alternative:** one `medications` table, or a list on the patient.

**Why:** the sidebar list is *what the patient was already taking when they
walked in*; the plan section is *what was prescribed today*. In the provided
chart meloxicam is absent from the July sidebar and present in the August one,
because it was prescribed in between. A patient-level medication list erases
exactly that, and a single table cannot hold both without lying about its grain.

The split also makes answerable: *was this patient already on an
anti-inflammatory when we prescribed another one?*

**Costs:** two tables where a reader might expect one, and an agent instruction
that has to keep them apart in prose. The views name them
`medications_on_arrival` and `prescriptions_written` so the distinction survives
into the query layer.

---

## 4. `drug_class` comes from a seeded lookup, not from the model

**Alternative:** ask the LLM which drugs are anti-inflammatories.

**Why:** answering *which patients on an anti-inflammatory…* requires knowing
that meloxicam is an NSAID. A seeded `ref_drug_class` table is deterministic,
auditable, reviewable by a pharmacist, and cannot hallucinate a class. The
warehouse resolves `prescriptions.drug_class` from it after every load, so the
lookup is the single source of truth and the fact table is still self-describing.

**Costs:** a drug outside the seed gets a NULL class rather than a guess. That
is the right failure: a missing classification is visible, an invented one is
not.

---

## 5. `ingestion_issues` is a warehouse table, not a log line

**Alternative:** write gaps to Cloud Logging.

**Why:** the brief requires that a chart missing a section still lands with the
gap *recorded*. Recording it in BigQuery makes "which charts are incomplete?" a
SQL question — answerable by the agent, demonstrable in the demo, and joinable
to the rows it describes. A log line is none of those.

Issue keys deliberately exclude the run id, so re-ingesting a chart updates the
same issue row instead of accumulating a new one per run. The run that most
recently observed the gap is kept in `ingest_run_id`.

**Costs:** one more table, and a discipline about what deserves a row. Sections
a visit note should always carry (chief complaint, assessment, plan, imaging)
and vitals produce a `warn` when absent; an optional field like a phone number
does not.

---

## 6. Both trigger paths, on one service

**Alternative:** pick one.

**Why:** they answer different needs and cost about fifteen lines together.
Eventarc on `object.finalized` is the production shape and is what makes the
demo real — a PDF lands in a bucket and rows appear. `POST /ingest` is how you
backfill, re-run a fixed parser over old charts, and drive an idempotency
demonstration without touching the bucket.

**Costs:** two routes to keep in step. They share `ingest_object()` entirely, so
there is one code path and two doors onto it.

---

## 7. Eventarc always gets a 2xx; the manual route does not

**Alternative:** return the real status code on both.

**Why:** a non-2xx tells Eventarc to redeliver. A chart that fails
deterministically — a corrupt PDF, a bug in a parser — would then retry forever
against a live billing account, and the failure would look like a cost problem
rather than a parsing problem. Failures are acknowledged and recorded in
`ingest_runs` and `ingestion_issues`, where they are queryable.

`POST /ingest` is called by a human, who should see a 500.

**Costs:** a silent-looking success in the HTTP layer. The audit tables are what
make it not silent, which is why `record_run` is wrapped so that a failure to
write the audit row cannot mask the outcome it was describing.

---

## 8. Geometry, not reading order, and not pixel constants

**Alternative:** parse the text stream PyMuPDF returns.

**Why:** the provided chart's header prints its labels on one row and their
values on the row beneath, right-aligned per cell:

```
PMS ID:   Sex:   DOB:        Phone:            MRN:
4820917   Male   09/15/1991  (615) 555-0173    4820917
```

Flattened to a string that reads `... MRN: 4820917 Male ...`, so a regex for
`MRN:\s*(\d+)` returns the **PMS ID**. It happens to be right on this chart
because both identifiers carry the same value — which is precisely the
coincidence that hides the bug. Pairing each label with the value block beneath
it that overlaps it horizontally reads the right cell every time.

The same argument applies to vitals, where a blank cell emits no text block at
all: anything reading by column position slides Ht, Wt, BMI and BSA under BP,
Pulse, Resp and O2.

Every boundary is derived from the page's own whitespace rather than a fixed
offset, so the parser survives a different page size. Two refinements were
forced by the real documents:

- A gutter is measured by **how many text rows cross it**, not by whether
  anything touches it. A full-width footer crosses the gutter on every page that
  has one, and a binary test let that single line erase the medication rail on
  every rendered chart.
- A sidebar must be **materially narrower** than the column beside it. Without
  that, the provided chart's two-column exam table reads as a rail and half of
  every exam lands on the wrong side of the body.

**Costs:** more code than a regex, and two constants (a 5% row tolerance, a 0.5
width ratio) that are ratios rather than pixels but are still tuned. Both are
named, commented, and covered by tests that fail if they drift.

---

## 9. The LLM writes exactly four columns

**Alternative:** let a model extract the whole chart, or fill gaps the parser
missed.

**Why:** identifiers, dates, ICD-10 codes, prescriptions with sig and quantity
and refills, vitals, provider and follow-up intervals are all things where being
wrong is unacceptable and a model cannot promise not to be. They are parsed
deterministically or left NULL.

What is left is prose with no structure to parse — which anatomic region this
visit is about, which side, whether it is a new problem or a follow-up, and a
one-sentence summary. That is a classification task, and it gets one
structured-output call per encounter at temperature 0 with an enum-constrained
schema. Values outside the enum are rejected; low confidence is recorded;
failure degrades to four NULLs plus an issue row and never fails the ingest.

**An LLM value never overwrites a parsed field.** `tests/test_pipeline.py`
asserts this by extracting the same chart twice, once with a stubbed classifier,
and requiring that nothing outside those four columns differs by a byte.

**Costs:** four columns are only as good as the model. They are scored
separately in `eval/report.md` so nobody has to take that on trust.

---

## 10. Two Cloud Run services, two service accounts

**Alternative:** one service doing both jobs.

**Why:** the ingester needs GCS read and BigQuery **write**; the agent needs
BigQuery **read** only. Separate service accounts with narrow grants is the
defensible answer on least privilege, and the two scale and fail differently —
ingestion is bursty and slow, querying is interactive.

**Costs:** two deploys, and the agent bundle needs `config.py`, which `adk
deploy` does not ship because it only packages `./agent`. Rather than duplicate
the module in git, `scripts/deploy_agent.sh` copies it to `agent/_config.py` at
deploy time and `.gitignore` keeps the copy out of the repository. `tools.py`
imports the real module first and falls back to the copy.

---

## 11. Buildpacks, not a Dockerfile

**Alternative:** write a Dockerfile.

**Why:** `gcloud run deploy --source .` builds through Cloud Build with
buildpacks, which read `requirements.txt` and the `Procfile`. No local Docker
daemon, no base image to keep patched, nothing to get wrong about a
non-root user.

**Costs:** less control over the image, and PyMuPDF needs a Python version with
a wheel — pinned by `.python-version`.

---

## 12. `document_id` is the content hash

**Alternative:** hash the bucket, object name and generation.

**Why:** re-uploading an identical chart under a new object name is the same
document, and should update one row rather than mint a second. Content identity
is also what makes the `documents` table a real audit anchor: two rows with the
same hash cannot exist, so "have we seen these bytes before?" is answerable.

**Costs:** `gcs_uri` records where the document was most recently seen, not
every place it has ever been. If a deployment needed the full location history,
this would become an `object_sightings` table rather than a column. The
clinical grain is unaffected either way, because encounters are keyed on the
visit, not the file.

---

## 13. Structured exam findings were built, not deferred

The design named `exam_findings` as the cut candidate: largest parsing job, no
rubric weight, none of the four required questions touch it.

It was built anyway, because the table was already declared in the DDL and a
declared table that is always empty is worse than either building it or
deleting it. What made it worth doing is that the provided chart prints exam
findings as a **two-column** table, right side and left side:

```
Right Shoulder Active ROM:      Left Shoulder Active ROM:
Forward Flexion: 180 degrees.   Forward Flexion: 170 degrees.
```

Read in plain reading order the columns interleave and every left-side
measurement is filed under the right shoulder — silently reporting the wrong
side of a patient's body. Recovering the columns first is the whole job, and it
is the kind of error that would never surface in a row count.

Findings that carry a number and a unit are typed (`rom_active`, `strength`,
`special_test`, …); prose exams, which is what the rendered corpus writes, are
typed `narrative` rather than forced into a measurement shape they do not have.

**Costs:** the largest single parser, for the least rubric weight. The
`narrative` type is a genuine admission that a prose exam is not structured
data, and querying it means reading text.

---

## 14. The parser refuses one fact the ground truth asserts

At the August visit the provided chart prints:

```
Plan: Prescription Medication Management.
Modify Regimen: Modify prescription medication therapy.
```

and nothing else — no drug, no strength, no sig, no quantity, no refill count.
The hand-labelled truth file records a meloxicam prescription there, carried
across from July, which is what a clinician reading both pages would infer.

The parser does not produce it. Writing those values into an August prescription
row would assert, as structured data, that a prescription with those exact terms
was issued on that date. Instead the encounter gets an `unparsed_field` warning
saying a prescribing action was recorded without printed dosing.

That choice costs eight scored fields and about ten points on the provided
chart's accuracy number. It is the right trade, and the report says so rather
than quietly rounding it away.

---

## 15. The circularity of scoring against self-generated charts

Seven of the eight charts were rendered from the JSON specs they are scored
against, so the parser and the generator share assumptions about layout. That
inflates the corpus number, and hiding it would read as luck rather than
judgment.

Three things bound it:

1. The provided chart came out of a real EMR, was not authored here, and is
   scored by the same harness against a hand-labelled truth file. Its layout
   differs from the rendered corpus in almost every respect — two-column exam
   tables, a header written as a label/value grid, sections whose headings share
   a line with their content, prescriptions with no heading at all — and every
   one of those differences was a bug first.
2. The rendered charts carry deliberate imperfections: a missing phone number, a
   chart with no vitals table, a chart with no imaging, an alternate provider.
   The parser has to handle absence rather than assume presence.
3. `follow_up_interval_days` is scored against an author-declared integer that is
   never rendered, so the parser has to locate the phrase inside a whole plan
   section rather than convert a string handed to it.

The honest residue: that follow-up ground truth was derived from the printed
phrase using the same units table the parser uses, so it tests phrase location
rather than arithmetic. `eval/report.md` says so too.
