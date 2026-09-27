#!/usr/bin/env bash
# Deploy do Cabe no Bolso no Cloud Run: Cloud Build -> Artifact Registry (agentes) -> Cloud Run (cabe-no-bolso).
# Rodar da raiz do repositório:
#   CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares ./deploy/deploy.sh
# A configuração `default` do gcloud é de outro cliente: use sempre a configuração `mana-gsoares` (conta vinculada ao
# projeto batalha-time-05-xew3), por CLOUDSDK_ACTIVE_CONFIG_NAME ou por `gcloud config configurations activate mana-gsoares`.
#
# Decisões (docs/decisoes, CLAUDE.md): um serviço, um worker, sessões em memória -> --min-instances=1 --max-instances=1
# --session-affinity; concorrência limitada (40) e sem chave no repo: o modelo é acessado via Vertex AI com a service
# account do Cloud Run (precisa do papel roles/aiplatform.user). DADOS=csv usa o CSV embarcado na imagem (plano B
# garantido); troque para DADOS=bigquery quando as views estiverem publicadas (roles/bigquery.jobUser + dataViewer).
# Pré-requisitos (uma vez): gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com
#                           gcloud artifacts repositories create agentes --repository-format=docker --location=us-central1
set -euo pipefail

cd "$(dirname "$0")/.."

PROJETO="${GOOGLE_CLOUD_PROJECT:-batalha-time-05-xew3}"
REGIAO="${REGIAO:-us-central1}"
SERVICO="${SERVICO:-cabe-no-bolso}"
REPOSITORIO="${REPOSITORIO:-agentes}"
IMAGEM="${REGIAO}-docker.pkg.dev/${PROJETO}/${REPOSITORIO}/${SERVICO}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"
MODELO="${MODELO:-gemini-3.8-flash}"
DADOS="${DADOS:-csv}"
MODO_CONVERSA="${MODO_CONVERSA:-auto}"   # auto: usa cabe_no_bolso.runtime se existir; sem_llm: só o núcleo

echo "== projeto ${PROJETO} | região ${REGIAO} | serviço ${SERVICO} | imagem ${IMAGEM}:${TAG}"
echo "== conta gcloud ativa: $(gcloud config get-value account 2>/dev/null || echo '?') (configuração: ${CLOUDSDK_ACTIVE_CONFIG_NAME:-$(gcloud config configurations list --filter=is_active:true --format='value(name)' 2>/dev/null)})"

# 1) imagem (contexto = raiz; .dockerignore deixa só config/, data/, demo/ e agent/)
gcloud builds submit --project "${PROJETO}" --region "${REGIAO}" --tag "${IMAGEM}:${TAG}" .

# 2) serviço
gcloud run deploy "${SERVICO}" \
  --project "${PROJETO}" \
  --image "${IMAGEM}:${TAG}" \
  --region="${REGIAO}" \
  --platform=managed \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=1 \
  --session-affinity \
  --concurrency=40 \
  --cpu=1 \
  --memory=1Gi \
  --timeout=120 \
  --port=8080 \
  --cpu-boost \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_LOCATION=global,GOOGLE_CLOUD_PROJECT=${PROJETO},DADOS=${DADOS},MODELO=${MODELO},MODO_CONVERSA=${MODO_CONVERSA},RAIZ=/app"

# 3) URL e QR code para o slide e o cartão impresso
URL="$(gcloud run services describe "${SERVICO}" --project "${PROJETO}" --region "${REGIAO}" --format='value(status.url)')"
echo
echo "== URL: ${URL}"
curl -fsS "${URL}/api/saude" && echo
if command -v uv >/dev/null 2>&1; then
  uv run --project agent python deploy/gerar_qr.py "${URL}" --saida deploy/qr-cabe-no-bolso.png || true
else
  python3 deploy/gerar_qr.py "${URL}" --saida deploy/qr-cabe-no-bolso.png || true
fi
echo "== logs: gcloud run services logs read ${SERVICO} --project ${PROJETO} --region ${REGIAO} --limit 50"
