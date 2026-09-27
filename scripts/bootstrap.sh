#!/usr/bin/env bash
# One-time, idempotent GCP setup for CI/CD: Artifact Registry, service accounts,
# IAM, BigQuery analytics dataset, GitHub connection and the Cloud Build trigger.
# The Agent Engine instance itself is created by the first build (scripts/agent_engine.py).
# Re-run it after authorizing the GitHub connection in the browser.
set -euo pipefail

PROJECT=${PROJECT:-$(gcloud config get-value project)}
REGION=us-central1
NAME=iai-cabe-no-bolso
GITHUB_REPO=https://github.com/guiwatanabe/$NAME.git
CONNECTION=github
RUN_SA=cabe-run@$PROJECT.iam.gserviceaccount.com
AGENT_SA=cabe-agent@$PROJECT.iam.gserviceaccount.com
BUILD_SA=cabe-build@$PROJECT.iam.gserviceaccount.com
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')

exists() { "$@" >/dev/null 2>&1; }
bind() { gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$1" --role="$2" --condition=None --quiet >/dev/null; }

echo "== APIs"
gcloud services enable --project "$PROJECT" \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com aiplatform.googleapis.com bigquery.googleapis.com iam.googleapis.com \
  cloudresourcemanager.googleapis.com logging.googleapis.com monitoring.googleapis.com \
  cloudtrace.googleapis.com telemetry.googleapis.com

echo "== Artifact Registry"
exists gcloud artifacts repositories describe "$NAME" --location "$REGION" --project "$PROJECT" ||
  gcloud artifacts repositories create "$NAME" --repository-format=docker --location "$REGION" --project "$PROJECT"

echo "== Service accounts"
for sa in cabe-run cabe-build cabe-agent; do
  exists gcloud iam service-accounts describe "$sa@$PROJECT.iam.gserviceaccount.com" --project "$PROJECT" ||
    gcloud iam service-accounts create "$sa" --project "$PROJECT"
done

# Cloud Run proxy: query Agent Engine, future secrets.
for role in roles/aiplatform.user roles/secretmanager.secretAccessor; do bind "$RUN_SA" "$role"; done

# Agent Engine runtime: Gemini on Vertex + managed sessions, BigQuery queries, logs.
for role in roles/aiplatform.user roles/bigquery.jobUser roles/logging.logWriter; do bind "$AGENT_SA" "$role"; done

# Build: deploy Cloud Run (incl. public IAM) and Agent Engine, push images, write logs, act as both runtime SAs.
for role in roles/run.admin roles/aiplatform.user roles/logging.logWriter; do bind "$BUILD_SA" "$role"; done
gcloud artifacts repositories add-iam-policy-binding "$NAME" --location "$REGION" --project "$PROJECT" \
  --member="serviceAccount:$BUILD_SA" --role=roles/artifactregistry.writer --quiet >/dev/null
for sa in "$RUN_SA" "$AGENT_SA"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" --project "$PROJECT" \
    --member="serviceAccount:$BUILD_SA" --role=roles/iam.serviceAccountUser --quiet >/dev/null
done

echo "== BigQuery analytics dataset"
exists bq --project_id "$PROJECT" show agent_logs || bq --project_id "$PROJECT" mk --location=US agent_logs
bq --project_id "$PROJECT" query --nouse_legacy_sql --quiet \
  "GRANT \`roles/bigquery.dataEditor\` ON SCHEMA \`$PROJECT.agent_logs\` TO 'serviceAccount:$AGENT_SA'" >/dev/null

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
    --service-account "projects/$PROJECT/serviceAccounts/$BUILD_SA" \
    --require-approval  # every collaborator can push to main; a human approves each deploy

# echo "== Budget alert"
# Emails billing admins at 50/90/100% of BUDGET_USD. Needs billing.budgets.create on the billing account.
# BUDGET_USD=${BUDGET_USD:-50}
# BILLING=$(gcloud billing projects describe "$PROJECT" --format='value(billingAccountName)' | cut -d/ -f2)
# gcloud services enable billingbudgets.googleapis.com --project "$PROJECT"
# if [[ -z $(gcloud billing budgets list --billing-account "$BILLING" --filter="displayName=$NAME" --format='value(name)') ]]; then
#  gcloud billing budgets create --billing-account "$BILLING" --display-name "$NAME" \
#    --budget-amount "${BUDGET_USD}USD" --filter-projects "projects/$PROJECT" \
#    --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0 ||
#    echo ">> Budget not created (no billing permission?). Create it in the console: Billing > Budgets & alerts."
#fi

# echo "Done. Push to main, then approve the build (Cloud Build > History), or:"
# echo "   gcloud builds triggers run deploy-main --region $REGION --branch main"
