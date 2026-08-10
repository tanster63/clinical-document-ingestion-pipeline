#!/usr/bin/env bash
# Deploys the ingest service and (re)creates its Eventarc trigger.
# Idempotent: safe to re-run after every change.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a
: "${GCP_PROJECT_ID:?}" "${BQ_DATASET:?}" "${GCS_BUCKET:?}"
REGION="${GCP_LOCATION:?set in .env}"
SERVICE="${INGEST_SERVICE_NAME:-chart-ingest}"
INGEST_SA="chart-ingest-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

echo "==> Deploying ${SERVICE} to ${REGION}"
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${GCP_PROJECT_ID}" \
  --region "${REGION}" \
  --service-account "${INGEST_SA}" \
  --no-allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 540 \
  --max-instances 5 \
  --set-env-vars "GCP_PROJECT_ID=${GCP_PROJECT_ID},BQ_DATASET=${BQ_DATASET},GCS_BUCKET=${GCS_BUCKET},GCP_LOCATION=${REGION},GEMINI_MODEL=${GEMINI_MODEL:-},PIPELINE_VERSION=${PIPELINE_VERSION:-}"

URL="$(gcloud run services describe "${SERVICE}" \
  --project "${GCP_PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
echo "==> Service URL: ${URL}"

echo "==> Ensuring Eventarc trigger"
TRIGGER="${SERVICE}-finalized"
if gcloud eventarc triggers describe "${TRIGGER}" \
     --project "${GCP_PROJECT_ID}" --location "${REGION}" >/dev/null 2>&1; then
  echo "    trigger already exists"
else
  # GCS publishes through Pub/Sub; its service agent needs the publisher role
  # once per project or trigger creation fails with PERMISSION_DENIED.
  STORAGE_AGENT="$(gcloud storage service-agent --project="${GCP_PROJECT_ID}")"
  gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
    --member="serviceAccount:${STORAGE_AGENT}" \
    --role="roles/pubsub.publisher" --condition=None >/dev/null

  gcloud eventarc triggers create "${TRIGGER}" \
    --project "${GCP_PROJECT_ID}" \
    --location "${REGION}" \
    --destination-run-service "${SERVICE}" \
    --destination-run-region "${REGION}" \
    --destination-run-path "/events" \
    --event-filters "type=google.cloud.storage.object.v1.finalized" \
    --event-filters "bucket=${GCS_BUCKET}" \
    --service-account "${INGEST_SA}"
fi

echo "==> Done. Smoke test:"
echo "    curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" ${URL}/healthz"
