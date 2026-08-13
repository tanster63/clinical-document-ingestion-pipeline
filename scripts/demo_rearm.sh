#!/usr/bin/env bash
# Reset between demo takes: verify the last ingest, clear the chart, empty the
# bucket. See scripts/demo_reset.py --rearm for what each step does and why the
# verify is allowed to stop the rest.
#
# This exists because the reset is the one command run repeatedly while
# rehearsing, and typing it wrong wastes a take. It sources .env itself, picks
# the virtualenv's python over whatever `python` resolves to, and takes the
# same flags as the script.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

py=.venv/bin/python
[ -x "$py" ] || py=python3

exec "$py" scripts/demo_reset.py --rearm "$@"
