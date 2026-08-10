#!/usr/bin/env bash
# Substitute ${PROJECT}/${DATASET} from .env and apply every DDL file, in order.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
: "${GCP_PROJECT_ID:?}" "${BQ_DATASET:?}" "${GCP_LOCATION:?}"

for f in sql/ddl/schema.sql sql/ddl/views.sql sql/ddl/seed_ref_drug_class.sql; do
  echo "applying $f"
  sed -e "s/\${PROJECT}/${GCP_PROJECT_ID}/g" -e "s/\${DATASET}/${BQ_DATASET}/g" "$f" \
    | bq query --location="$GCP_LOCATION" --use_legacy_sql=false --project_id="$GCP_PROJECT_ID"
done
echo "schema applied to ${GCP_PROJECT_ID}:${BQ_DATASET}"
