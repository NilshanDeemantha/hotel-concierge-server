#!/usr/bin/env bash
# Production release: Firestore rules first, then the functions.
# Rules go first so the database is locked down before any new code runs.
# Extra arguments are passed to both deploys, for example: ./scripts/deploy.sh --account you@example.com
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT="thambili-bay-concierge"

# The Firebase CLI reads the function list from this virtual environment.
if [ ! -x functions/venv/bin/python ]; then
  python3.13 -m venv functions/venv
  functions/venv/bin/pip install --quiet -r functions/requirements.txt
fi

echo "==> Deploying Firestore rules"
firebase deploy --only firestore:rules --project "$PROJECT" --non-interactive "$@"

echo "==> Deploying functions"
# --force removes functions that no longer exist in the code and keeps the image cleanup policy set.
firebase deploy --only functions --project "$PROJECT" --non-interactive --force "$@"
