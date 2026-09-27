#!/usr/bin/env bash
# Orçamento do grupo no Cloud Billing com alertas em 50 / 80 / 100% (config/finops.yaml: travas.orcamento_grupo_usd e
# travas.alertas_orcamento_pct). NÃO foi executado na madrugada: `gcloud billing budgets create` exige papel na CONTA DE
# FATURAMENTO (roles/billing.costsManager ou roles/billing.admin), que não está entre os papéis do Maná no projeto
# (run.admin, cloudbuild.builds.editor, artifactregistry.writer, iam.serviceAccountUser, bigquery.admin, secretmanager.admin,
# modelarmor.user, conferidos em 27/09 01h). Quem tiver o papel na conta de faturamento do evento roda uma vez.
#
#   CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares ./deploy/orcamento.sh               # cria o orçamento (precisa do papel de billing)
#   CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares ./deploy/orcamento.sh --mostrar     # só imprime o comando, sem executar
#
# O alerta chega por e-mail aos administradores de faturamento; para avisar o time, ligue --notifications-rule-monitoring-notification-channels
# com um canal do Monitoring (e-mail/Slack). Enquanto o orçamento não existe, o controle é manual: Console > Billing > Reports,
# filtrado pelo projeto batalha-time-05-xew3 (item 3 do checklist de docs/11-finops.md §6).
set -euo pipefail

PROJETO="${GOOGLE_CLOUD_PROJECT:-batalha-time-05-xew3}"
VALOR_USD="${ORCAMENTO_USD:-1000}"          # config/finops.yaml: travas.orcamento_grupo_usd
NOME="${ORCAMENTO_NOME:-batalha-time-05-cabe-no-bolso}"

# conta de faturamento do projeto (leitura de metadados; falha se a conta ativa não puder ler o projeto)
CONTA="${BILLING_ACCOUNT:-$(gcloud billing projects describe "${PROJETO}" --format='value(billingAccountName)' 2>/dev/null | sed 's#billingAccounts/##')}"
if [[ -z "${CONTA}" ]]; then
  echo "conta de faturamento não encontrada para ${PROJETO}: exporte BILLING_ACCOUNT=<id> (Console > Billing)." >&2
  [[ "${1:-}" == "--mostrar" ]] || exit 1
  CONTA="<BILLING_ACCOUNT_ID>"
fi

CMD=(gcloud billing budgets create
  --billing-account="${CONTA}"
  --display-name="${NOME}"
  --budget-amount="${VALOR_USD}USD"
  --filter-projects="projects/${PROJETO}"
  --threshold-rule=percent=0.5,basis=current-spend      # 50%  (config/finops.yaml: alertas_orcamento_pct)
  --threshold-rule=percent=0.8,basis=current-spend      # 80%
  --threshold-rule=percent=1.0,basis=current-spend      # 100%
  --threshold-rule=percent=1.0,basis=forecasted-spend   # previsão de estourar antes do fim do mês
  --calendar-period=month)

echo "== orçamento ${NOME}: US\$ ${VALOR_USD}/mês na conta ${CONTA}, projeto ${PROJETO}, alertas 50 / 80 / 100% (+ previsão)"
printf '   %q' "${CMD[@]}"; echo
if [[ "${1:-}" == "--mostrar" ]]; then
  echo "== não executado (--mostrar)."
  exit 0
fi
"${CMD[@]}"
echo
echo "== conferir: gcloud billing budgets list --billing-account=${CONTA}"
