#!/usr/bin/env bash
# Creates two Google Cloud Scheduler jobs that trigger Atlas's automated
# Schoology sync on Cloud Run, twice a day. This is the Cloud Run equivalent
# of the `crons` entries in vercel.json — Cloud Run has no cron of its own,
# so Cloud Scheduler is what actually calls the endpoint on a schedule.
#
# Run this once your backend is deployed to Cloud Run and ATLAS_CRON_SECRET
# is set on that service (see automation/README.md).
#
# Usage:
#   PROJECT_ID=my-gcp-project \
#   CLOUD_RUN_URL=https://atlas-backend-xyz.a.run.app \
#   CRON_SECRET=the-same-value-as-ATLAS_CRON_SECRET \
#   ./automation/cloud-scheduler-setup.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to your GCP project id}"
: "${CLOUD_RUN_URL:?Set CLOUD_RUN_URL to the deployed backend's URL (no trailing slash)}"
: "${CRON_SECRET:?Set CRON_SECRET to the same value as the backend's ATLAS_CRON_SECRET}"
LOCATION="${LOCATION:-us-east1}"

gcloud scheduler jobs create http atlas-schoology-sync-morning \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="0 6 * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/integrations/cron/schoology/sync" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --description="Atlas: morning Schoology sync (all connected users)"

gcloud scheduler jobs create http atlas-schoology-sync-afternoon \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="0 16 * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/integrations/cron/schoology/sync" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --description="Atlas: afternoon Schoology sync (all connected users)"

echo "Created. Verify with:"
echo "  gcloud scheduler jobs list --project=$PROJECT_ID --location=$LOCATION"
echo "Run one immediately with:"
echo "  gcloud scheduler jobs run atlas-schoology-sync-morning --project=$PROJECT_ID --location=$LOCATION"
