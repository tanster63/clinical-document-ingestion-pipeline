#!/usr/bin/env bash
# Enable APIs, create the bucket, dataset, and two least-privilege service accounts.
# Idempotent: safe to re-run.
set -euo pipefail
set -a; source "$(dirname "$0")/../.env"; set +a

: "${GCP_PROJECT_ID:?set in .env}" "${GCS_BUCKET:?}" "${BQ_DATASET:?}" "${GCP_LOCATION:?}"

gcloud config set project "$GCP_PROJECT_ID"

gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com eventarc.googleapis.com \
  bigquery.googleapis.com storage.googleapis.com aiplatform.googleapis.com \
  artifactregistry.googleapis.com pubsub.googleapis.com

gcloud storage buckets describe "gs://${GCS_BUCKET}" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${GCS_BUCKET}" \
    --location="$GCP_LOCATION" --uniform-bucket-level-access

bq --location="$GCP_LOCATION" show "${GCP_PROJECT_ID}:${BQ_DATASET}" >/dev/null 2>&1 || \
  bq --location="$GCP_LOCATION" mk --dataset \
    --description="Cumberland Orthopedics clinical warehouse (synthetic data)" \
    "${GCP_PROJECT_ID}:${BQ_DATASET}"

# Ingester: read GCS, write BigQuery. Agent: read BigQuery only. (§3)
for SA in chart-ingest-sa chart-agent-sa; do
  gcloud iam service-accounts describe \
    "${SA}@${GCP_PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$SA" --display-name="$SA"
done

INGEST_SA="chart-ingest-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
AGENT_SA="chart-agent-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
  --member="serviceAccount:${INGEST_SA}" --role=roles/storage.objectViewer
for ROLE in roles/bigquery.dataEditor roles/bigquery.jobUser roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:${INGEST_SA}" --role="$ROLE" --condition=None >/dev/null
done
for ROLE in roles/bigquery.dataViewer roles/bigquery.jobUser roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:${AGENT_SA}" --role="$ROLE" --condition=None >/dev/null
done

echo "infra ready: gs://${GCS_BUCKET}, ${GCP_PROJECT_ID}:${BQ_DATASET}"
