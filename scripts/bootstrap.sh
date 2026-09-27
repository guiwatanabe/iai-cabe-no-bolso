#!/usr/bin/env bash
# One-time, idempotent GCP setup for CI/CD: Artifact Registry, service accounts,
# IAM, BigQuery analytics dataset, GitHub connection and the Cloud Build trigger.
# Re-run it after authorizing the GitHub connection in the browser.
set -euo pipefail

PROJECT=${PROJECT:-$(gcloud config get-value project)}
REGION=us-central1
NAME=iai-cabe-no-bolso
GITHUB_REPO=https://github.com/guiwatanabe/$NAME.git
CONNECTION=github
RUN_SA=cabe-run@$PROJECT.iam.gserviceaccount.com
BUILD_SA=cabe-build@$PROJECT.iam.gserviceaccount.com
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')

exists() { "$@" >/dev/null 2>&1; }
bind() { gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$1" --role="$2" --condition=None --quiet >/dev/null; }

echo "== APIs"
gcloud services enable --project "$PROJECT" \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com aiplatform.googleapis.com bigquery.googleapis.com iam.googleapis.com

echo "== Artifact Registry"
exists gcloud artifacts repositories describe "$NAME" --location "$REGION" --project "$PROJECT" ||
  gcloud artifacts repositories create "$NAME" --repository-format=docker --location "$REGION" --project "$PROJECT"

echo "== Service accounts"
for sa in cabe-run cabe-build; do
  exists gcloud iam service-accounts describe "$sa@$PROJECT.iam.gserviceaccount.com" --project "$PROJECT" ||
    gcloud iam service-accounts create "$sa" --project "$PROJECT"
done

# Runtime: Gemini on Vertex, BigQuery queries, future secrets.
for role in roles/aiplatform.user roles/bigquery.jobUser roles/secretmanager.secretAccessor; do bind "$RUN_SA" "$role"; done

# Build: deploy Cloud Run (incl. public IAM), push images, write logs, act as the runtime SA.
for role in roles/run.admin roles/logging.logWriter; do bind "$BUILD_SA" "$role"; done
gcloud artifacts repositories add-iam-policy-binding "$NAME" --location "$REGION" --project "$PROJECT" \
  --member="serviceAccount:$BUILD_SA" --role=roles/artifactregistry.writer --quiet >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA" --project "$PROJECT" \
  --member="serviceAccount:$BUILD_SA" --role=roles/iam.serviceAccountUser --quiet >/dev/null

echo "== BigQuery analytics dataset"
exists bq --project_id "$PROJECT" show agent_logs || bq --project_id "$PROJECT" mk --location=US agent_logs
bq --project_id "$PROJECT" query --nouse_legacy_sql --quiet \
  "GRANT \`roles/bigquery.dataEditor\` ON SCHEMA \`$PROJECT.agent_logs\` TO 'serviceAccount:$RUN_SA'" >/dev/null

echo "== GitHub connection"
# The Cloud Build service agent stores the GitHub OAuth token in Secret Manager.
bind "service-$PROJECT_NUMBER@gcp-sa-cloudbuild.iam.gserviceaccount.com" roles/secretmanager.admin
exists gcloud builds connections describe "$CONNECTION" --region "$REGION" --project "$PROJECT" ||
  gcloud builds connections create github "$CONNECTION" --region "$REGION" --project "$PROJECT"

stage=$(gcloud builds connections describe "$CONNECTION" --region "$REGION" --project "$PROJECT" --format='value(installationState.stage)')
if [[ "$stage" != COMPLETE ]]; then
  gcloud builds connections describe "$CONNECTION" --region "$REGION" --project "$PROJECT" --format='value(installationState.actionUri)'
  echo ">> Open the URL above, authorize the Cloud Build GitHub App for $GITHUB_REPO, then re-run this script."
  exit 1
fi

exists gcloud builds repositories describe "$NAME" --connection "$CONNECTION" --region "$REGION" --project "$PROJECT" ||
  gcloud builds repositories create "$NAME" --remote-uri "$GITHUB_REPO" --connection "$CONNECTION" --region "$REGION" --project "$PROJECT"

echo "== Trigger"
exists gcloud builds triggers describe deploy-main --region "$REGION" --project "$PROJECT" ||
  gcloud builds triggers create github --name deploy-main --region "$REGION" --project "$PROJECT" \
    --repository "projects/$PROJECT/locations/$REGION/connections/$CONNECTION/repositories/$NAME" \
    --branch-pattern '^main$' --build-config cloudbuild.yaml \
    --service-account "projects/$PROJECT/serviceAccounts/$BUILD_SA"

echo "Done. Push to main (or: gcloud builds triggers run deploy-main --region $REGION --branch main)."
