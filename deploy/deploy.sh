#!/usr/bin/env bash
# Deploy do Cabe no Bolso no Cloud Run: Cloud Build -> Artifact Registry (agentes) -> Cloud Run (cabe-no-bolso).
# Rodar da raiz do repositório:
#   CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares ./deploy/deploy.sh
# A configuração `default` do gcloud é de outro cliente: use sempre a configuração `mana-gsoares` (conta vinculada ao
# projeto batalha-time-05-xew3), por CLOUDSDK_ACTIVE_CONFIG_NAME ou por `gcloud config configurations activate mana-gsoares`.
#
# Este script é o caminho do dia: roda com as credenciais do Maná (run.admin, cloudbuild.builds.editor,
# artifactregistry.writer e iam.serviceAccountUser na squad-agent-sa, conferidos em 27/09 01h). O mesmo pipeline como CI
# (test -> build -> push -> deploy, com aprovação manual) está em deploy/cloudbuild.yaml:
#   gcloud builds submit --config deploy/cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD),_MIN_INSTANCES=1 .
#
# Identidade (docs/12-identidade-e-seguranca.md): o serviço roda como
# squad-agent-sa@batalha-time-05-xew3.iam.gserviceaccount.com (roles/aiplatform.user, bigquery.jobUser, logging.logWriter,
# monitoring.metricWriter, secretmanager.secretAccessor; conferido em 27/09 01h). A service account padrão do Compute NÃO
# tem aiplatform.user: com ela o modelo falha e a sessão cai para sem_llm. Zero chaves JSON: o modelo é acessado via
# Vertex AI com a identidade do próprio Cloud Run (ADC).
#
# Decisões (docs/decisoes, CLAUDE.md): um serviço, um worker, sessões em memória -> --max-instances=1 --session-affinity;
# --min-instances=${MIN_INSTANCES} (padrão 1 no dia da demo: sem cold start na frente da banca; 0 fora dela, ver
# config/finops.yaml); concorrência limitada (40). DADOS=csv usa o CSV embarcado na imagem (plano B garantido); troque
# para DADOS=bigquery para ler hackathon_dados.extrato_sintetico; o CSV continua como queda automática se o BigQuery falhar.
# Ordem completa do dia, rollback e o que conferir antes e depois: deploy/CHECKLIST.md.
# Pré-requisitos (conferidos em 27/09 01h: APIs run, cloudbuild, artifactregistry, aiplatform, bigquery, secretmanager e
# logging já ligadas; repositório "agentes" já existe; squad-agent-sa com os papéis acima):
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com
#   gcloud artifacts repositories create agentes --repository-format=docker --location=us-central1
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
# Latência no palco (medida em 27/09 02h, modo gi com validador: 6,8 a 8,2 s por turno; 16,6 s com 1 regeneração). Duas alavancas,
# ambas sem tocar no código (docs/11-finops.md §7):
#   MODELO_VALIDADOR=gemini-2.5-flash ./deploy/deploy.sh   validador num modelo mais rápido/barato (preço confirmado em config/finops.yaml:
#                                                          US$ 0,30 / 2,50 por milhão); o agente continua no gemini-3.8-flash
#   VALIDADOR_ATIVO=false ./deploy/deploy.sh               desliga o segundo LlmAgent: ficam as checagens em código + guardião (1 chamada por
#                                                          turno, ~4 s); o painel mostra "validador desligado" em cada turno
VALIDADOR_ATIVO="${VALIDADOR_ATIVO:-true}"
DADOS="${DADOS:-csv}"
# Analytics do agente no BigQuery (BigQueryAgentAnalyticsPlugin do ADK; padrão trazido de guiwatanabe/iai-cabe-no-bolso).
# Desligado na demo: exige o dataset criado (deploy/CHECKLIST.md: bq mk --dataset --location=us-central1 batalha-time-05-xew3:agent_logs),
# o extra `bigquery` na imagem (uv sync --extra bigquery) e bigquery.dataEditor + jobUser na SA de runtime. Para ligar, descomente
# a linha abaixo e acrescente ",BQ_ANALYTICS_DATASET=${BQ_ANALYTICS_DATASET}" ao --set-env-vars.
# BQ_ANALYTICS_DATASET="${BQ_ANALYTICS_DATASET:-agent_logs}"
# MODO_CONVERSA: o padrão vem de agent/.env.example (única fonte da escolha gi | tools | auto | sem_llm e do porquê);
# exporte MODO_CONVERSA para sobrepor. sem_llm = só o núcleo (plano B, zero chamadas ao modelo).
MODO_CONVERSA="${MODO_CONVERSA:-$(grep -E '^MODO_CONVERSA=' agent/.env.example 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]')}"
MODO_CONVERSA="${MODO_CONVERSA:-auto}"
RATE_LIMIT_POR_MINUTO="${RATE_LIMIT_POR_MINUTO:-120}"   # por IP em /api; a banca compartilha o IP do Wi-Fi do evento
MAX_CONCORRENCIA="${MAX_CONCORRENCIA:-40}"
MIN_INSTANCES="${MIN_INSTANCES:-1}"                      # 1 no dia da demo; MIN_INSTANCES=0 fora dela (ou passo 13 do CHECKLIST)
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-squad-agent-sa@${PROJETO}.iam.gserviceaccount.com}"   # SA de runtime (docs/12); nunca a padrão do Compute
BIGQUERY_TABELA="${BIGQUERY_TABELA:-${PROJETO}.hackathon_dados.extrato_sintetico}"

echo "== projeto ${PROJETO} | região ${REGIAO} | serviço ${SERVICO} | imagem ${IMAGEM}:${TAG}"
echo "== modelo ${MODELO} (validador ${MODELO_VALIDADOR}) | dados ${DADOS} | modo ${MODO_CONVERSA} | rate limit ${RATE_LIMIT_POR_MINUTO}/min"
echo "== service account ${SERVICE_ACCOUNT} | min-instances ${MIN_INSTANCES} | max-instances 1 | concorrência ${MAX_CONCORRENCIA}"
echo "== conta gcloud ativa: $(gcloud config get-value account 2>/dev/null || echo '?') (configuração: ${CLOUDSDK_ACTIVE_CONFIG_NAME:-$(gcloud config configurations list --filter=is_active:true --format='value(name)' 2>/dev/null)})"

# 1) imagem (contexto = raiz; .gcloudignore decide o que sobe, .dockerignore deixa só config/, data/, demo/ e agent/)
gcloud builds submit --project "${PROJETO}" --region "${REGIAO}" --tag "${IMAGEM}:${TAG}" .

# 2) serviço (mesmos flags de deploy/cloudbuild.yaml)
gcloud run deploy "${SERVICO}" \
  --project "${PROJETO}" \
  --image "${IMAGEM}:${TAG}" \
  --region="${REGIAO}" \
  --platform=managed \
  --service-account="${SERVICE_ACCOUNT}" \
  --allow-unauthenticated \
  --min-instances="${MIN_INSTANCES}" \
  --max-instances=1 \
  --session-affinity \
  --concurrency="${MAX_CONCORRENCIA}" \
  --cpu=1 \
  --memory=1Gi \
  --timeout=120 \
  --port=8080 \
  --cpu-boost \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_LOCATION=global,GOOGLE_CLOUD_PROJECT=${PROJETO},DADOS=${DADOS},BIGQUERY_TABELA=${BIGQUERY_TABELA},MODELO=${MODELO},MODELO_VALIDADOR=${MODELO_VALIDADOR},VALIDADOR_ATIVO=${VALIDADOR_ATIVO},MODO_CONVERSA=${MODO_CONVERSA},RATE_LIMIT_POR_MINUTO=${RATE_LIMIT_POR_MINUTO},MAX_CONCORRENCIA=${MAX_CONCORRENCIA},RAIZ=/app"

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
echo "== rollback: gcloud run revisions list --service ${SERVICO} --project ${PROJETO} --region ${REGIAO}"
echo "            gcloud run services update-traffic ${SERVICO} --project ${PROJETO} --region ${REGIAO} --to-revisions=<revisão-anterior>=100"
