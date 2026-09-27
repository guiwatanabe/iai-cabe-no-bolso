#!/usr/bin/env bash
# Manual deploy with your gcloud credentials: tests -> Agent Engine -> proxy image -> Cloud Run.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=${PROJECT:-batalha-time-05-xew3}
REGION=us-central1
SERVICE=iai-cabe-no-bolso
RUNTIME_SA=squad-agent-sa@$PROJECT.iam.gserviceaccount.com
IMAGE=$REGION-docker.pkg.dev/$PROJECT/agentes/cabe-proxy:$(git rev-parse --short HEAD)

git fetch -q origin main
if [[ -n $(git status --porcelain) || $(git rev-parse HEAD) != $(git rev-parse origin/main) ]]; then
  echo "Deploy only a clean checkout of origin/main (commit, push and pull first)." >&2
  exit 1
fi

# adk deploy uses agents/cabe/.env instead of the config's env_vars and ships the file with the agent.
if [[ -e agents/cabe/.env ]]; then
  echo "Move agents/cabe/.env to ./.env (adk web still finds it) before deploying." >&2
  exit 1
fi

uv run --frozen ruff check
uv run --frozen ruff format --check
uv run --frozen pytest -q

# BigQueryAgentAnalyticsPlugin target, in the same location as hackathon_dados.
bq --project_id "$PROJECT" show agent_logs >/dev/null 2>&1 ||
  bq --project_id "$PROJECT" mk --location="$REGION" agent_logs

engine=$(uv run --frozen python scripts/agent_engine.py "$PROJECT" "$REGION" "$RUNTIME_SA")

# An update still running (another deploy, or an interrupted one) makes adk deploy fail with FAILED_PRECONDITION.
ops=https://$REGION-aiplatform.googleapis.com/v1beta1/$engine/operations
while :; do
  pending=$(curl -fsS -H "Authorization: Bearer $(gcloud auth print-access-token)" "$ops" |
    jq '[.operations[]? | select(.done != true)] | length')
  ((pending == 0)) && break
  echo "Agent Engine is busy with another update; waiting..." >&2
  sleep 30
done
uv run --frozen adk deploy agent_engine --project="$PROJECT" --region="$REGION" --agent_engine_id="$engine" agents/cabe

gcloud builds submit --project "$PROJECT" --region "$REGION" --tag "$IMAGE" .

gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --image "$IMAGE" \
  --service-account "$RUNTIME_SA" --allow-unauthenticated \
  --min-instances 1 --max-instances 3 --cpu-boost \
  --set-env-vars "AGENT_ENGINE=$engine"
