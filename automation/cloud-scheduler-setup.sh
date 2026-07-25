#!/usr/bin/env bash
# Creates Google Cloud Scheduler jobs that trigger Atlas's automated
# PowerSchool + Schoology syncs on Cloud Run, twice a day each. This is the
# Cloud Run equivalent of the `crons` entries in vercel.json — Cloud Run has
# no cron of its own, so Cloud Scheduler is what actually calls the endpoint
# on a schedule.
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
  --schedule="0 7 * * *" \
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

gcloud scheduler jobs create http atlas-powerschool-sync-morning \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="0 7 * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/integrations/cron/powerschool/sync" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --description="Atlas: morning PowerSchool sync (all connected users)"

gcloud scheduler jobs create http atlas-powerschool-sync-afternoon \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="0 16 * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/integrations/cron/powerschool/sync" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --description="Atlas: afternoon PowerSchool sync (all connected users)"

# Deleting a document in the app queues its R2 file for removal after a
# 24-hour grace period instead of deleting it immediately (see
# app.services.storage_cleanup) — this sweep is what actually removes
# whatever's aged past that window. Once a day is plenty.
gcloud scheduler jobs create http atlas-storage-cleanup \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="0 9 * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/documents/cron/purge-deleted" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --description="Atlas: purge R2 files whose 24h delete grace period has passed"

# Finishes indexing (chunk/embed) + AI-enriching any document still sitting
# at ingested:false. This used to happen inline with the upload request
# (then, briefly, as a FastAPI BackgroundTask) — both failed in practice on
# Cloud Run, which only allocates CPU to a container while it's actively
# serving a request, so a background task kept alive past the response
# being sent can simply never finish. A real request on a schedule always
# gets genuine CPU. Every 2 minutes so an upload finishes processing
# promptly without ever tying up a single request for long.
gcloud scheduler jobs create http atlas-document-processing \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="*/2 * * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/documents/cron/process-pending" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --description="Atlas: finish indexing + AI-enriching any pending document"

echo "Created. Verify with:"
echo "  gcloud scheduler jobs list --project=$PROJECT_ID --location=$LOCATION"
echo "Run one immediately with:"
echo "  gcloud scheduler jobs run atlas-schoology-sync-morning --project=$PROJECT_ID --location=$LOCATION"
