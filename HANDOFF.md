# Handoff

Written for whoever picks this up next. It says what is done, what is not, where
the bodies are buried, and what to do first.

**Read the assessment brief before changing anything.** `ZCS_Data_Engineer_Take_Home.pdf`
arrived with the hiring email and is deliberately not committed here. It is the
first source of truth for what this has to do; the provided sample chart,
`charts/source/EMA_20250723T140400_...pdf`, is the second. Everything in this
repository is downstream of those two documents, and where this repo and the
brief disagree, the brief wins.

---

## State in one paragraph

The pipeline is complete and verified locally: eight chart PDFs extract into a
fourteen-table BigQuery schema with zero errors, 303 tests pass, extraction
accuracy is 100% on the authored corpus and 90.2% on the provided chart, and
re-ingesting is idempotent under both a verbatim re-run and a re-export under
different file names. **Nothing has been deployed to Google Cloud.** The deploy
scripts are written and idempotent but unexecuted, so every claim about Cloud
Run, Eventarc, live BigQuery and the agent's actual answers is unverified.

---

## What is done

| Deliverable | State |
| --- | --- |
| §6.1 Eight chart PDFs | Done. `charts/source/` (provided) + `charts/generated/` (7 authored, rendered from `corpus/specs/`). Committed. Not yet uploaded to a bucket. |
| §6.2 Ingestion pipeline | Code complete. FastAPI, two trigger paths, idempotent MERGE. Not deployed. |
| §6.3 Structured dataset | Schema complete and documented (`sql/ddl/`, `docs/schema.md`). Not applied to a live dataset. |
| §6.4 Query agent | Code complete (`agent/`). Four tools, guarded SQL. Never run against a live warehouse. |
| §6.5 Repository | README, architecture diagram, schema doc, decision log, clean commit history. |
| §6.6 Demo video | **Not started.** |

---

## What to do first

1. **Deploy.** Follow [DEPLOYMENT.md](DEPLOYMENT.md) end to end. Budget an hour;
   most of it is Cloud Build. The step that usually fights back is the Eventarc
   trigger's Pub/Sub permission, and it is documented.
2. **Run the live test.** `RUN_LIVE_TESTS=1 pytest tests/test_warehouse_live.py`.
   This is the only thing that can prove idempotency against BigQuery itself —
   load-job visibility to `MERGE` is a property of the service, not of this code.
   If it fails, the fix is in `ingestion/keys.py`, not in the test.
3. **Score the LLM columns.** `python -m eval.accuracy --llm` with credentials.
   The committed report deliberately leaves those four columns unscored rather
   than publishing a 0% that measures a missing API key.
4. **Run the agent** through [`eval/questions.md`](eval/questions.md). Every
   expected answer there is measured from the shipped corpus, so a divergence is
   a real defect. If an answer is ungrounded, fix `INSTRUCTION` in
   `agent/agent.py` — never hardcode an answer.
5. **Record the video.** Notes below.

---

## Repo tour

```
ingestion/          the pipeline
  config.py         the ONLY module that reads the environment
  keys.py           deterministic keys — the whole idempotency story
  models.py         Pydantic contracts, one per table, names mirror the DDL
  warehouse.py      staging load job -> MERGE -> orphan sweep -> derived columns
  app.py            FastAPI: /events (always 2xx), /ingest (5xx on failure)
  extract/
    layout.py       page geometry: header band, left rail, body
    sections.py     section headings, including ones that wrap or share a line
    encounters.py   splits one PDF into visits
    pipeline.py     orchestration; the only module that knows the order
    fields/         one parser per fact type
    llm.py          the single Gemini call, four columns, temperature 0

agent/              ADK agent: 4 read-only tools over 2 views
corpus/             chart authoring: specs -> exam template -> Jinja/WeasyPrint
sql/ddl/            the warehouse definition; the single source of truth
eval/               measured accuracy + the four brief questions
scripts/            infra, DDL, deploys, and a local end-to-end run
tests/              303 tests
```

**Start reading at** `ingestion/extract/pipeline.py`. It is the spine and it
names every other module in import order.

---

## Things that will bite you

**The provided chart's header is a grid, not a sentence.** Labels on one row,
values on the row beneath, right-aligned. Flattened to text it reads
`... MRN: 4820917 Male ...` where that number is the *PMS ID*. It is right on
this chart only because both identifiers carry the same value. Identity is
therefore paired by horizontal overlap in `fields/identifiers.py`. Do not
"simplify" that back into a regex over the header text.

**A blank vitals cell emits no text block at all.** Anything reading by column
position slides Ht, Wt, BMI and BSA under BP, Pulse, Resp and O2. Vitals are
paired to labels by overlap for the same reason.

**The exam is two columns, right side and left side.** Read in plain reading
order they interleave, and every left-side measurement gets filed under the right
shoulder — silently reporting the wrong side of a patient's body.
`fields/exam.py` recovers the columns before anything else.

**`docs/schema.md` is checked against the DDL.** `tests/test_schema_contract.py`
parses `sql/ddl/schema.sql` and fails if a model field, a merge key or the doc
drifts. A BigQuery load job matches on column name, so a drifted field lands
NULL rather than raising — nothing else would catch it until a query came back
empty. If you add a column, expect that test to tell you what else to update.

**The corpus must keep looking like the sample.** §5.2 requires it, and
`tests/test_render.py` asserts the authored charts print the sample's literal
section labels. If you change `corpus/templates/chart.html.j2`, re-render and
re-run `python -m eval.accuracy`; the corpus is scored against the specs it was
rendered from, and a template change usually breaks a parser before it breaks a
test.

**Re-render is byte-deterministic.** An unchanged spec produces an identical
PDF. If `git status` shows a chart changed and you did not mean it to, something
non-deterministic crept into the renderer.

---

## Known gaps, in the order I would fix them

1. **Nothing is deployed.** Everything about the cloud path is unverified.
2. **No agent transcript exists.** The answers in `eval/questions.md` are
   computed from the warehouse, not recorded from a model turn.
3. **`write_document` is not atomic.** Each table merges in its own statement, so
   a mid-document failure can leave it half-written. The run records `failed`
   and a re-ingest converges, but the right fix is to wrap the merges in a single
   BigQuery transaction.
4. **`icd10_description` is the chart's wording, not a canonical label.** Group
   conditions by `icd10_code`. A seeded `ref_icd10` table would fix it, in the
   same shape as `ref_drug_class`.
5. **The exam's `narrative` finding type is an admission**, not a design: prose
   exams are stored as sentences because they are not structured data.
6. **Scanned charts are out of scope.** Every chart here is real text. The moment
   one arrives as an image, this becomes a Document AI problem — see the
   technology-choices table in the README.

---

## The one deliberate refusal

At the August visit the provided chart prints:

```
Plan: Prescription Medication Management.
Modify Regimen: Modify prescription medication therapy.
```

and no drug, strength, sig, quantity or refill count. The hand-labelled truth
file records a meloxicam prescription there, carried across from July.

**The parser does not produce it,** and that is on purpose. Writing those values
in would assert as structured data that a prescription with those exact terms was
issued on that date. Instead the encounter carries an `unparsed_field` warning
saying a prescribing action was recorded without printed dosing. It costs eight
scored fields and about ten points on that chart's accuracy.

If you are tempted to "fix" the number, read
[decision 14](docs/decisions.md) first. Inventing a dose is the one failure this
pipeline is built not to have.

---

## Demo video notes (§6.6, 5–8 minutes)

The brief asks for three things: a PDF entering the bucket and rows appearing in
BigQuery, the agent answering at least three questions including one spanning
multiple tables, and a brief explanation of the schema.

Suggested cut:

| Time | Beat |
| --- | --- |
| 0:00–0:45 | The problem, the architecture diagram, what was built |
| 0:45–2:15 | **Schema first** — it is 30% of the grade. The encounter grain, why one PDF is two visits, and the two grains in the left rail |
| 2:15–3:30 | Live ingest: `gcloud storage cp` a chart, show the Eventarc run land in `ingest_runs`, then the rows |
| 3:30–4:15 | Re-upload the same chart; show the counts do not move |
| 4:15–6:15 | The agent: one question of each kind, including "which patients on an anti-inflammatory had imaging on the same day", plus the blood-pressure trap answered "not recorded" |
| 6:15–7:15 | Measured accuracy, the deterministic/LLM split, and the honest note about self-generated charts |
| 7:15–7:45 | What was deferred and what would change at real volume |

Lead with the schema. It is the largest single component of the grade and the
part a data engineer is actually being evaluated on.
