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
# garantido); troque para DADOS=bigquery para ler hackathon_dados.extrato_sintetico (roles/bigquery.jobUser + dataViewer
# na service account); o CSV continua como queda automática se o BigQuery falhar.
# Ordem completa do dia, com o que conferir antes e depois: deploy/CHECKLIST.md.
# Pré-requisitos (conferidos em 27/09 01h: APIs run, cloudbuild, artifactregistry, aiplatform, bigquery, secretmanager e
# logging já ligadas; repositório "agentes" já existe):
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com
#   gcloud artifacts repositories create agentes --repository-format=docker --location=us-central1
#   gcloud projects add-iam-policy-binding batalha-time-05-xew3 --role=roles/aiplatform.user \
#     --member="serviceAccount:${SERVICE_ACCOUNT:-602056186697-compute@developer.gserviceaccount.com}"
set -euo pipefail

cd "$(dirname "$0")/.."

PROJETO="${GOOGLE_CLOUD_PROJECT:-batalha-time-05-xew3}"
REGIAO="${REGIAO:-us-central1}"
SERVICO="${SERVICO:-cabe-no-bolso}"
REPOSITORIO="${REPOSITORIO:-agentes}"
IMAGEM="${REGIAO}-docker.pkg.dev/${PROJETO}/${REPOSITORIO}/${SERVICO}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"
MODELO="${MODELO:-gemini-3.8-flash}"
MODELO_VALIDADOR="${MODELO_VALIDADOR:-${MODELO}}"   # segundo LlmAgent (validador da Gi); padrão = o mesmo modelo
DADOS="${DADOS:-csv}"
# MODO_CONVERSA: o padrão vem de agent/.env.example (única fonte da escolha gi | tools | auto | sem_llm e do porquê);
# exporte MODO_CONVERSA para sobrepor. sem_llm = só o núcleo (plano B, zero chamadas ao modelo).
MODO_CONVERSA="${MODO_CONVERSA:-$(grep -E '^MODO_CONVERSA=' agent/.env.example 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]')}"
MODO_CONVERSA="${MODO_CONVERSA:-auto}"
RATE_LIMIT_POR_MINUTO="${RATE_LIMIT_POR_MINUTO:-120}"   # por IP em /api; a banca compartilha o IP do Wi-Fi do evento
MAX_CONCORRENCIA="${MAX_CONCORRENCIA:-40}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-}"                   # vazio = service account padrão do Compute (precisa de roles/aiplatform.user)
BIGQUERY_TABELA="${BIGQUERY_TABELA:-${PROJETO}.hackathon_dados.extrato_sintetico}"

echo "== projeto ${PROJETO} | região ${REGIAO} | serviço ${SERVICO} | imagem ${IMAGEM}:${TAG}"
echo "== modelo ${MODELO} (validador ${MODELO_VALIDADOR}) | dados ${DADOS} | modo ${MODO_CONVERSA} | rate limit ${RATE_LIMIT_POR_MINUTO}/min"
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
  ${SERVICE_ACCOUNT:+--service-account="${SERVICE_ACCOUNT}"} \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_LOCATION=global,GOOGLE_CLOUD_PROJECT=${PROJETO},DADOS=${DADOS},BIGQUERY_TABELA=${BIGQUERY_TABELA},MODELO=${MODELO},MODELO_VALIDADOR=${MODELO_VALIDADOR},MODO_CONVERSA=${MODO_CONVERSA},RATE_LIMIT_POR_MINUTO=${RATE_LIMIT_POR_MINUTO},MAX_CONCORRENCIA=${MAX_CONCORRENCIA},RAIZ=/app"

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
