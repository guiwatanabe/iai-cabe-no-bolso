#!/usr/bin/env bash
# Métricas baseadas em log (Cloud Logging -> Cloud Monitoring) do Cabe no Bolso: custo diário em USD, latência p95 e
# mensagens seguras, a partir do log JSON que o servidor escreve por turno (agent/server/finops.py: evento "turno", uma
# linha por papel: agente | validador | codigo; campos custo_usd, latencia_ms, tokens_entrada, tokens_saida,
# mensagem_segura, validador_aprovado, regeneracoes; sem PII nem conteúdo do extrato).
#
# Rodar uma vez, com a configuração do Maná (a `default` do gcloud é de outro cliente):
#   CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares ./deploy/metricas.sh            # cria (ou atualiza) as 4 métricas
#   CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares ./deploy/metricas.sh --listar   # só lista o que existe
# Papel necessário: roles/logging.configWriter (ou logging.admin) no projeto. A SA de runtime (squad-agent-sa) só precisa
# de logging.logWriter, que já tem: ela escreve as linhas; quem cria a métrica é uma pessoa, uma vez.
#
# Como ler no Monitoring (Metrics Explorer, recurso "Cloud Run Revision", métrica logging/user/<nome>):
#   custo diário   cabe_custo_usd (distribuição): alinhamento "sum" por 1 dia, agrupado por rótulo papel/modelo
#   latência p95   cabe_latencia_ms (distribuição): alinhamento "percentile 95" por 5 min, agrupado por papel
#   msgs seguras   cabe_mensagens_seguras (contador): alinhamento "sum" por 1 h
#   tokens         cabe_tokens (distribuição): "sum" por 1 dia, agrupado por papel e sentido (entrada/saída via 2 métricas abaixo)
# Consulta direta no Logs Explorer (custo da última hora por papel):
#   jsonPayload.evento="turno" AND resource.type="cloud_run_revision" AND resource.labels.service_name="cabe-no-bolso"
set -euo pipefail

PROJETO="${GOOGLE_CLOUD_PROJECT:-batalha-time-05-xew3}"
SERVICO="${SERVICO:-cabe-no-bolso}"
FILTRO_BASE="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SERVICO}\" AND jsonPayload.evento=\"turno\""

if [[ "${1:-}" == "--listar" ]]; then
  gcloud logging metrics list --project "${PROJETO}" --filter="name:cabe_" --format="table(name,description,filter)"
  exit 0
fi

echo "== projeto ${PROJETO} | serviço ${SERVICO} | conta $(gcloud config get-value account 2>/dev/null || echo '?')"

criar_ou_atualizar() {  # nome, descrição, filtro, [extras...]
  local nome="$1" descricao="$2" filtro="$3"; shift 3
  if gcloud logging metrics describe "${nome}" --project "${PROJETO}" >/dev/null 2>&1; then
    gcloud logging metrics update "${nome}" --project "${PROJETO}" --description="${descricao}" --log-filter="${filtro}" "$@"
  else
    gcloud logging metrics create "${nome}" --project "${PROJETO}" --description="${descricao}" --log-filter="${filtro}" "$@"
  fi
}

# 1) custo em USD por turno (distribuição; some por dia no Monitoring). Rótulos: papel (agente|validador|codigo) e modelo.
criar_ou_atualizar cabe_custo_usd \
  "Cabe no Bolso: custo em USD de cada chamada ao modelo por turno (tokens x preço de config/finops.yaml). Somar por dia." \
  "${FILTRO_BASE} AND jsonPayload.custo_usd:*" \
  --value-extractor='EXTRACT(jsonPayload.custo_usd)' \
  --label-extractors='papel=EXTRACT(jsonPayload.papel),modelo=EXTRACT(jsonPayload.modelo)' \
  --bucket-type=exponential --exponential-num-finite-buckets=32 --exponential-growth-factor=2 --exponential-scale=0.000001

# 2) latência por turno em ms (distribuição; p95 no Monitoring). Rótulo: papel.
criar_ou_atualizar cabe_latencia_ms \
  "Cabe no Bolso: latência (ms) por turno com modelo; usar alinhamento percentile 95." \
  "${FILTRO_BASE} AND jsonPayload.llm=true AND jsonPayload.latencia_ms:*" \
  --value-extractor='EXTRACT(jsonPayload.latencia_ms)' \
  --label-extractors='papel=EXTRACT(jsonPayload.papel),modelo=EXTRACT(jsonPayload.modelo)' \
  --bucket-type=exponential --exponential-num-finite-buckets=24 --exponential-growth-factor=1.5 --exponential-scale=100

# 3) mensagens seguras (contador): turnos em que a resposta do modelo foi trocada pelo texto fixo (checagens ou validador).
criar_ou_atualizar cabe_mensagens_seguras \
  "Cabe no Bolso: turnos que saíram como mensagem segura (checagens em código ou validador reprovaram)." \
  "${FILTRO_BASE} AND jsonPayload.mensagem_segura=true AND jsonPayload.papel!=\"validador\""

# 4) tokens de entrada por turno (distribuição; somar por dia). Rótulo: papel.
criar_ou_atualizar cabe_tokens \
  "Cabe no Bolso: tokens de entrada por chamada (papel agente ou validador); somar por dia. Saída: jsonPayload.tokens_saida." \
  "${FILTRO_BASE} AND jsonPayload.llm=true AND jsonPayload.tokens_entrada:*" \
  --value-extractor='EXTRACT(jsonPayload.tokens_entrada)' \
  --label-extractors='papel=EXTRACT(jsonPayload.papel),modelo=EXTRACT(jsonPayload.modelo)' \
  --bucket-type=exponential --exponential-num-finite-buckets=20 --exponential-growth-factor=2 --exponential-scale=64

# 5) regenerações e validador reprovado (contadores; a taxa de reprovação é a segunda dividida pelos turnos com modelo)
criar_ou_atualizar cabe_regeneracoes \
  "Cabe no Bolso: turnos com pelo menos uma regeneração pedida pelo validador ou pelas checagens." \
  "${FILTRO_BASE} AND jsonPayload.papel=\"agente\" AND jsonPayload.regeneracoes>0"
criar_ou_atualizar cabe_validador_reprovou \
  "Cabe no Bolso: turnos em que o veredito final do validador foi reprovado." \
  "${FILTRO_BASE} AND jsonPayload.papel=\"agente\" AND jsonPayload.validador_aprovado=false"

echo
echo "== métricas criadas/atualizadas (logging/user/cabe_*):"
gcloud logging metrics list --project "${PROJETO}" --filter="name:cabe_" --format="table(name,description)"
echo
echo "== alerta sugerido (custo > US\$ 20/dia), criar no Console > Monitoring > Alerting com a métrica logging/user/cabe_custo_usd"
echo "   (alinhamento sum, janela 1 dia) ou por política JSON; orçamento do grupo com alertas 50/80/100%: deploy/orcamento.sh"
