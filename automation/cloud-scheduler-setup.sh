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

# --attempt-deadline: Cloud Scheduler's default is 180s (3 min) if
# unspecified, which is already shorter than the backend's own per-user sync
# budget (SYNC_TIMEOUT_SECONDS = 270s in app.integrations) — and
# run_sync_for_all syncs every connected user for the provider sequentially
# in one request, so the real time needed only grows with more users and
# more content per user (more courses/materials to walk). Set to 1800s, the
# maximum Cloud Scheduler allows for an HTTP target, so the scheduler's own
# deadline is never what cuts a sync short — the backend's own timeouts
# (which *do* still apply, and which chunk/resume a long Schoology sync
# rather than losing all its progress — see SchoologyProvider.sync) are.
gcloud scheduler jobs create http atlas-schoology-sync-morning \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="0 7 * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/integrations/cron/schoology/sync" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --attempt-deadline=1800s \
  --description="Atlas: morning Schoology sync (all connected users)"

gcloud scheduler jobs create http atlas-schoology-sync-afternoon \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="0 16 * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/integrations/cron/schoology/sync" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --attempt-deadline=1800s \
  --description="Atlas: afternoon Schoology sync (all connected users)"

gcloud scheduler jobs create http atlas-powerschool-sync-morning \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="0 7 * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/integrations/cron/powerschool/sync" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --attempt-deadline=1800s \
  --description="Atlas: morning PowerSchool sync (all connected users)"

gcloud scheduler jobs create http atlas-powerschool-sync-afternoon \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="0 16 * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/integrations/cron/powerschool/sync" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --attempt-deadline=1800s \
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

# Safety net only, not the primary path: the frontend now calls
# POST /documents/{id}/process itself right after an upload finishes (and
# via the documents page's "Index now" button), so most documents never
# reach this job at all — see app.routers.documents' module docstring. This
# only catches whatever neither of those ever reached (tab closed mid-
# upload, a dropped network request, etc.), so it runs every 15 minutes
# instead of the every-2-minutes cadence it needed back when it was the
# only path — cuts Cloud Run invocations from this job by ~87% while still
# catching a stuck document promptly.
gcloud scheduler jobs create http atlas-document-processing \
  --project="$PROJECT_ID" \
  --location="$LOCATION" \
  --schedule="*/15 * * * *" \
  --time-zone="America/New_York" \
  --uri="${CLOUD_RUN_URL}/api/v1/documents/cron/process-pending" \
  --http-method=GET \
  --headers="X-Cron-Secret=${CRON_SECRET}" \
  --description="Atlas: safety-net sweep for any document neither the post-upload call nor Index now button reached"

echo "Created. Verify with:"
echo "  gcloud scheduler jobs list --project=$PROJECT_ID --location=$LOCATION"
echo "Run one immediately with:"
echo "  gcloud scheduler jobs run atlas-schoology-sync-morning --project=$PROJECT_ID --location=$LOCATION"
