#!/usr/bin/env bash
# Deploys the ADK agent to Cloud Run under its own read-only service account.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a
: "${GCP_PROJECT_ID:?}" "${BQ_DATASET:?}"
REGION="${GCP_LOCATION:?set in .env}"
SERVICE="${AGENT_SERVICE_NAME:-chart-agent}"
AGENT_SA="chart-agent-sa@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

# adk ships only ./agent, so give it the one module it borrows from ingestion.
cp ingestion/config.py agent/_config.py

adk deploy cloud_run \
  --project "${GCP_PROJECT_ID}" \
  --region "${REGION}" \
  --service_name "${SERVICE}" \
  --app_name clinical_query_agent \
  --with_ui \
  ./agent

echo "==> Pinning the service account and environment"
gcloud run services update "${SERVICE}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${REGION}" \
  --service-account "${AGENT_SA}" \
  --set-env-vars "GCP_PROJECT_ID=${GCP_PROJECT_ID},BQ_DATASET=${BQ_DATASET},GCS_BUCKET=${GCS_BUCKET},GCP_LOCATION=${REGION},GEMINI_MODEL=${GEMINI_MODEL:-}"

gcloud run services describe "${SERVICE}" \
  --project "${GCP_PROJECT_ID}" --region "${REGION}" --format='value(status.url)'
