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
fourteen-table BigQuery schema with zero errors, 321 tests pass, extraction
accuracy is 100% on the authored corpus and 90.2% on the provided chart, and
re-ingesting is idempotent under both a verbatim re-run and a re-export under
different file names.

**It is now deployed and verified on Google Cloud.** All eight charts were
ingested through the Eventarc path into live BigQuery, and the warehouse
reproduces the measured answers in `eval/questions.md` exactly — the body-region
distribution, the five anti-inflammatory-plus-imaging encounters, and Trey
Barlow's meloxicam prescription all match row for row. Re-uploading all eight
charts afterwards produced exactly eight ingest runs and changed no row count in
any table, which is idempotency demonstrated against the service rather than
asserted.

One blemish is worth recording because of what it taught rather than because it
survives: `exam_findings` held 585 rows against 551 distinct `finding_id` — 34
byte-identical duplicates left by a redelivery storm from before the Eventarc
ack deadline was raised (see the deploy script). `MERGE` converges but never
deletes, so they sat there. They have since been removed and the table reads
551/551, which is what `scripts/run_local.py` produces locally. Nothing in the
pipeline can create more of them; the ack deadline is the actual fix.

---

## What is done

| Deliverable | State |
| --- | --- |
| §6.1 Eight chart PDFs | Done. `charts/source/` (provided) + `charts/generated/` (7 authored, rendered from `corpus/specs/`). Committed and uploaded to `gs://<project>-charts-raw/incoming/`. |
| §6.2 Ingestion pipeline | Done and deployed. Cloud Run service `chart-ingest`, Eventarc trigger `chart-ingest-finalized`, idempotent MERGE verified live. |
| §6.3 Structured dataset | Done. 14 tables + 2 views applied to the live `cumberland` dataset and populated from all eight charts. |
| §6.4 Query agent | Done and deployed. Cloud Run service `chart-agent`, four tools, guarded SQL, driven through all nine `eval/questions.md` prompts against the live warehouse — all nine answer correctly and in full. |
| §6.5 Repository | README, architecture diagram, schema doc, decision log, clean commit history. |
| §6.6 Demo video | **Not recorded.** The runbook is written — see [docs/demo_brief.md](docs/demo_brief.md) — and the reset that makes a live ingest filmable has been proven end to end against the deployed services. |

---

## What to do first

1. **Nothing, to get it running.** [DEPLOYMENT.md](DEPLOYMENT.md) has been run
   end to end, the four defects it exposed are fixed in the scripts, and both
   services are live. The list below is what to do to keep it honest.
2. **Run the live test.** `RUN_LIVE_TESTS=1 pytest tests/test_warehouse_live.py`.
   This is the only thing that can prove idempotency against BigQuery itself —
   load-job visibility to `MERGE` is a property of the service, not of this code.
   If it fails, the fix is in `ingestion/keys.py`, not in the test.
3. **Score the LLM columns.** `python -m eval.accuracy --llm` with credentials.
   The committed report deliberately leaves those four columns unscored rather
   than publishing a 0% that measures a missing API key.
4. **Re-run the agent** through [`eval/questions.md`](eval/questions.md) after
   any change to `INSTRUCTION` or the views. It has been driven through all nine
   prompts against the live warehouse and answers all nine correctly and in
   full; the cost of the open-ended one is gap #1 below. Every expected answer
   in that file is measured from the shipped corpus, so a divergence is a real
   defect. If an
   answer is ungrounded, fix `INSTRUCTION` in `agent/agent.py` — never hardcode
   an answer.
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
tests/              321 tests
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

**A view can be created, described, committed — and be unreadable.** `CREATE
VIEW` validates the SQL without running it, so `v_patient_timeline` shipped with
correlated `ARRAY(SELECT ... WHERE child.encounter_id = e.encounter_id)`
subqueries that BigQuery refuses at *query* time: "Correlated subqueries that
reference other tables are not supported unless they can be de-correlated". Even
`SELECT *` failed. Nothing caught it because no test had ever read from a view —
`tests/test_warehouse_live.py` now selects from every view in `ALLOWED_VIEWS`,
and that is the test to keep. Build nested arrays with `ARRAY_AGG` in a CTE and
`LEFT JOIN` them, then `IFNULL(x, [])`: a correlated subquery returns an empty
array for a childless parent, a `LEFT JOIN` returns NULL, and callers were
written against the empty array.

**Re-render is byte-deterministic.** An unchanged spec produces an identical
PDF. If `git status` shows a chart changed and you did not mean it to, something
non-deterministic crept into the renderer.

---

## Known gaps, in the order I would fix them

1. **The open-ended question costs 17 tool calls.** It now answers in full —
   the ranking, then medications, procedures and imaging for each of the top
   five — but it gets there with three separate queries per condition rather
   than one grouped query. Correct and slow. A worked example in the
   instruction, or a `condition_treatments` view, would collapse it.
2. **`write_document` is not atomic.** Each table merges in its own statement, so
   a mid-document failure can leave it half-written. The run records `failed`
   and a re-ingest converges, but the right fix is to wrap the merges in a single
   BigQuery transaction.
3. **`icd10_description` is the chart's wording, not a canonical label.** Group
   conditions by `icd10_code`. A seeded `ref_icd10` table would fix it, in the
   same shape as `ref_drug_class`.
4. **The exam's `narrative` finding type is an admission**, not a design: prose
   exams are stored as sentences because they are not structured data.
5. **Scanned charts are out of scope.** Every chart here is real text. The moment
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

**[docs/demo_brief.md](docs/demo_brief.md) is the operational version of this** —
the same cut with the actual commands, the pre-flight checks, the expected row
counts either side of the ingest, and the failure modes that ruin a take. The
table below is the shape; that file is how you run it.

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
