#!/usr/bin/env bash
# Row counts across the tables the demo touches, in one shot.
#
# Run it before the live ingest and again after. The same command twice, with
# the numbers moved, is the clearest statement that a PDF became structured
# data. A single COUNT(*) is not: nobody watching knows whether 15 is right.
#
# Sorted by grain rather than alphabetically -- one document is one patient is
# two encounters is four diagnoses -- so the shape of the jump reads correctly
# on camera.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
: "${GCP_PROJECT_ID:?}" "${BQ_DATASET:?}"

t="${GCP_PROJECT_ID}.${BQ_DATASET}"

bq query --use_legacy_sql=false --format=pretty "
SELECT tbl, n FROM (
  SELECT 1 AS ord, 'documents'      AS tbl, COUNT(*) AS n FROM \`${t}.documents\`      UNION ALL
  SELECT 2, 'patients',           COUNT(*) FROM \`${t}.patients\`            UNION ALL
  SELECT 3, 'encounters',         COUNT(*) FROM \`${t}.encounters\`          UNION ALL
  SELECT 4, 'diagnoses',          COUNT(*) FROM \`${t}.diagnoses\`           UNION ALL
  SELECT 5, 'prescriptions',      COUNT(*) FROM \`${t}.prescriptions\`       UNION ALL
  SELECT 6, 'exam_findings',      COUNT(*) FROM \`${t}.exam_findings\`
) ORDER BY ord"
