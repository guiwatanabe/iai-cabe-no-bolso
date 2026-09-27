#!/usr/bin/env bash
# OPCIONAL. Publica só o agente (cabe_no_bolso/) no Vertex AI Agent Engine com `adk deploy agent_engine`.
# NÃO é o caminho da demo: a demo é o Cloud Run de deploy/deploy.sh (API + demo estática + agente no mesmo serviço,
# sessão em memória). Este script serve para mostrar o agente na Agent Platform do projeto (Dev UI, sessões gerenciadas)
# ou como plano C se o Cloud Run falhar e a banca aceitar o playground. Não executar antes do deploy.sh estar no ar.
#
# Rodar da raiz:
#   CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares ./deploy/deploy_agent_engine.sh
# Flags conferidas em `uv run adk deploy agent_engine --help` (ADK 2.10.0): --project, --region, --display_name,
# --description, --extra_packages (repetível), --agent_engine_id (atualizar em vez de criar), --temp_folder, --otel_to_cloud.
# Limites conhecidos:
# - O agente importa cabe_core e lê config/taxas.yaml, data/extrato_sintetico.csv.gz e data/personas/: por isso os
#   diretórios entram via --extra_packages e o código resolve RAIZ; se o Agent Engine não tiver os arquivos, o agente
#   sobe mas as ferramentas falham ("dados indisponíveis").
# - O modelo gemini-3.8-flash responde só em GOOGLE_CLOUD_LOCATION=global; o Agent Engine é regional (us-central1).
#   O runtime do agente continua chamando o modelo em global (variável do .env). Conferir na Agent Platform.
# - Sem chave no repo: ADC local para o deploy; no Agent Engine, a service account do Reasoning Engine precisa de
#   roles/aiplatform.user.
set -euo pipefail

cd "$(dirname "$0")/.."

PROJETO="${GOOGLE_CLOUD_PROJECT:-batalha-time-05-xew3}"
REGIAO="${REGIAO:-us-central1}"
NOME="${NOME:-cabe-no-bolso}"
AGENTE_ID="${AGENT_ENGINE_ID:-}"          # vazio cria uma instância nova; preenchido atualiza a existente
TEMP="${TEMP_FOLDER:-/tmp/cabe-no-bolso-agent-engine}"

echo "== projeto ${PROJETO} | região ${REGIAO} | agente ${NOME} | conta: $(gcloud config get-value account 2>/dev/null || echo '?')"

ARGS=(
  --project "${PROJETO}"
  --region "${REGIAO}"
  --display_name "${NOME}"
  --description "Cabe no Bolso: agente que tira o cliente da fatura rolada com um plano que cabe no mês (Time 05)."
  --temp_folder "${TEMP}"
  --extra_packages agent/cabe_core
  --extra_packages config
  --extra_packages data
)
if [[ -n "${AGENTE_ID}" ]]; then
  ARGS+=(--agent_engine_id "${AGENTE_ID}")
fi

# `adk deploy agent_engine` recebe a pasta do agente (a que contém agent.py com root_agent).
( cd agent && uv run adk deploy agent_engine "${ARGS[@]}" cabe_no_bolso )

echo
echo "== conferir na Agent Platform: https://console.cloud.google.com/vertex-ai/agents/agent-engines?project=${PROJETO}"
echo "== a demo continua no Cloud Run (deploy/deploy.sh); este agente é opcional."
