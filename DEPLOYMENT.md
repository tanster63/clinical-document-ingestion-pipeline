# Deployment

Everything here runs on Google Cloud's free trial credit. These steps have been
executed end to end against a live project: all eight charts were ingested by
the Eventarc path and the warehouse counts below are what came back. Where a
step is known to be fragile, that is called out rather than smoothed over.

Four defects only surfaced during that first real run, and each is fixed in the
scripts here rather than described as a workaround: the buildpack builder no
longer offers Python 3.11; the ingest service account was missing the two roles
Eventarc delivery requires; `gcloud storage service-agent` pads its output with
whitespace that IAM then rejects; and Eventarc's Pub/Sub subscription defaults
to a 10s ack deadline, far under the time a chart takes to ingest.

Total time from a clean project: roughly 30 minutes, most of it waiting for two
Cloud Build runs.

---

## 0. Prerequisites

```bash
gcloud --version          # Google Cloud CLI, authenticated
bq version                # ships with the CLI
python3 --version         # 3.11 or newer; 3.13 is what the buildpack uses
pip install google-adk    # provides the `adk` command used in step 5
```

```bash
gcloud auth login
gcloud auth application-default login    # the libraries read these credentials
gcloud config set project <PROJECT_ID>
```

Billing must be enabled on the project — Cloud Run, Eventarc and Vertex AI all
refuse to enable otherwise, with an error that does not mention billing.

---

## 1. Configure

```bash
cp .env.example .env
```

Fill in:

| Variable | Value | Notes |
| --- | --- | --- |
| `GCP_PROJECT_ID` | your project id | |
| `GCS_BUCKET` | globally unique bucket name | e.g. `<project>-charts-raw` |
| `BQ_DATASET` | `cumberland` | anything, as long as it is consistent |
| `GCP_LOCATION` | `us-central1` | Cloud Run, Eventarc, BigQuery and Vertex must agree |
| `GEMINI_MODEL` | `gemini-2.5-flash` | optional; `config.py` defaults to it |
| `PIPELINE_VERSION` | `0.1.0` | stamped onto every `documents` row |

`.env` is gitignored. No project id, bucket name or dataset name appears in any
source file — `ingestion/config.py` is the only module that reads them, and
`tests/test_schema_contract.py` fails if one is ever committed.

```bash
set -a; source .env; set +a
```

---

## 2. Infrastructure

```bash
./scripts/setup_infra.sh
```

Enables the APIs, creates the bucket and the dataset, and creates two service
accounts with deliberately different grants:

| Service account | Grants | Why |
| --- | --- | --- |
| `chart-ingest-sa` | `storage.objectViewer` on the bucket, `bigquery.dataEditor`, `bigquery.jobUser`, `aiplatform.user` | reads charts, writes rows, calls Gemini |
| `chart-agent-sa` | `bigquery.dataViewer`, `bigquery.jobUser`, `aiplatform.user` | reads rows only — it cannot write to the warehouse even if the SQL guard were bypassed |

Idempotent: safe to re-run.

---

## 3. Schema

```bash
./scripts/apply_ddl.sh
```

Substitutes `${PROJECT}` and `${DATASET}` and applies, in order:
`sql/ddl/schema.sql` (14 tables), `sql/ddl/views.sql` (2 views),
`sql/ddl/seed_ref_drug_class.sql` (the drug-class lookup, merged so it can be
re-run).

Verify:

```bash
bq ls "$GCP_PROJECT_ID:$BQ_DATASET"          # expect 14 tables + 2 views
bq query --use_legacy_sql=false \
  "SELECT COUNT(*) AS drugs FROM \`$GCP_PROJECT_ID.$BQ_DATASET.ref_drug_class\`"
```

Re-running `apply_ddl.sh` after a schema change is safe for new tables and new
columns, but `CREATE TABLE IF NOT EXISTS` will **not** alter an existing table.
If a column changed type, drop that table first.

---

## 4. Upload the charts

```bash
gcloud storage cp charts/source/*.pdf charts/generated/*.pdf \
  "gs://$GCS_BUCKET/incoming/"
```

Do this *before* deploying if you want a clean first run, or *after* if you want
to watch Eventarc fire.

---

## 5. Deploy the ingest service

```bash
./scripts/deploy_ingest.sh
```

Builds from source with buildpacks (no Dockerfile), deploys to Cloud Run as
`chart-ingest` under `chart-ingest-sa`, then creates the Eventarc trigger on
`google.cloud.storage.object.v1.finalized` pointed at `POST /events`.

**Expect this to be the step that fights back.** Two known issues:

1. *Eventarc trigger creation fails with `PERMISSION_DENIED` on Pub/Sub.* GCS
   publishes through Pub/Sub and its service agent needs
   `roles/pubsub.publisher` once per project. The script grants it before
   creating the trigger; if the grant has not propagated yet, wait a minute and
   re-run the script.
2. *The build fails resolving Python.* Google's buildpack builder dropped 3.11,
   which is what this originally pinned. `.python-version` now says 3.13;
   PyMuPDF ships stable-ABI (`cp39-abi3`) wheels, so it does not care which of
   those it gets. Confirm the file was not excluded by `.gcloudignore`.

Smoke test:

```bash
URL=$(gcloud run services describe chart-ingest \
        --project "$GCP_PROJECT_ID" --region "$GCP_LOCATION" \
        --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)

curl -s -H "Authorization: Bearer $TOKEN" "$URL/health"
```

The service is deployed `--no-allow-unauthenticated`; every call needs that
identity token.

---

## 6. Ingest

**Automatically** — drop a file in the bucket and Eventarc fires:

```bash
gcloud storage cp charts/source/*.pdf "gs://$GCS_BUCKET/incoming/"
sleep 45
```

**Manually** — same code path, no bucket write, which is how you re-run a chart
after fixing a parser:

```bash
curl -s -X POST "$URL/ingest" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"object":"incoming/EMA_20250723T140400_0000_MRN4820917_PMS4820917_PID18442091_PatientChart_400112.pdf"}' \
  | python -m json.tool
```

Expect `"status": "succeeded"`, `"encounters": 2`, `"errors": 0`.

---

## 7. Verify the warehouse

```bash
bq query --use_legacy_sql=false "
SELECT
  (SELECT COUNT(*) FROM \`$GCP_PROJECT_ID.$BQ_DATASET.patients\`)             AS patients,
  (SELECT COUNT(*) FROM \`$GCP_PROJECT_ID.$BQ_DATASET.encounters\`)           AS encounters,
  (SELECT COUNT(*) FROM \`$GCP_PROJECT_ID.$BQ_DATASET.diagnoses\`)            AS diagnoses,
  (SELECT COUNT(*) FROM \`$GCP_PROJECT_ID.$BQ_DATASET.prescriptions\`)        AS prescriptions,
  (SELECT COUNT(*) FROM \`$GCP_PROJECT_ID.$BQ_DATASET.patient_history\`)      AS history,
  (SELECT COUNT(*) FROM \`$GCP_PROJECT_ID.$BQ_DATASET.ingestion_issues\`
     WHERE severity='error')                                                  AS errors"
```

After all eight charts, expect exactly what `scripts/run_local.py` prints
locally: **8 patients, 15 encounters, 23 diagnoses, 15 prescriptions, 28 history
rows, 0 errors.** A difference between the local run and BigQuery is a real
finding — the two use the same `rows_for()` and `MERGE_KEYS`.

**Prove idempotency** — the point of the whole key design:

```bash
gcloud storage cp charts/source/*.pdf "gs://$GCS_BUCKET/incoming/"   # again
sleep 45
# re-run the count query above: every number is unchanged
```

Then the test that only real BigQuery can run:

```bash
RUN_LIVE_TESTS=1 pytest tests/test_warehouse_live.py -v
```

Five assertions, including that no `_stg_*` staging table was left behind and
that `encounter_seq` is correct warehouse-wide after the merge.

---

## 8. Deploy the agent

```bash
./scripts/deploy_agent.sh
```

Copies `ingestion/config.py` to `agent/_config.py` (the ADK bundle ships only
`./agent`; the copy is gitignored), runs `adk deploy cloud_run --with_ui`, then
pins the read-only service account and the environment.

Open the printed URL and work through [`eval/questions.md`](eval/questions.md).
The expected answers there are measured from the shipped corpus, so any
divergence is a real defect and not a matter of taste.

---

## 9. What the LLM columns need

`body_region`, `laterality`, `visit_type` and `hpi_summary` are the only
model-derived columns. They fill in automatically once the deployed service has
`aiplatform.user`, because the ingest path builds a Vertex client by default.

To score them:

```bash
set -a; source .env; set +a
python -m eval.accuracy --llm     # rewrites eval/report.md with both tables
```

Without credentials those four columns stay NULL and the report says so rather
than publishing a 0%.

---

## Cost and teardown

Eight charts, fifteen Gemini calls and a handful of BigQuery jobs sit inside the
free tier. The one thing that accrues is an idle Cloud Run service, which scales
to zero — `--max-instances 5` caps a runaway.

```bash
gcloud run services delete chart-ingest --region "$GCP_LOCATION"
gcloud run services delete chart-agent  --region "$GCP_LOCATION"
gcloud eventarc triggers delete chart-ingest-finalized --location "$GCP_LOCATION"
bq rm -r -f "$GCP_PROJECT_ID:$BQ_DATASET"
gcloud storage rm -r "gs://$GCS_BUCKET"
```

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `403` from Vertex on the first ingest | ingest SA missing `aiplatform.user` | re-run `./scripts/setup_infra.sh` |
| Eventarc row never appears in `ingest_runs` | trigger built but the service rejected the call | `gcloud run services logs read chart-ingest --region "$GCP_LOCATION" --limit 50` |
| `/events` returns 200 with `"status": "failed"` | working as designed — a non-2xx would make Eventarc redeliver for ever | read `ingest_runs.error_detail` |
| `MERGE must match at most one source row` | two rows sharing a merge key | should be impossible — `rows_for()` deduplicates — so report it with the chart |
| Row counts grow on re-ingest | a key is being derived from something document-scoped | fix `ingestion/keys.py`, not the test |
| `ModuleNotFoundError: ingestion` on agent deploy | `agent/_config.py` was not copied | run `./scripts/deploy_agent.sh` rather than `adk deploy` directly |
