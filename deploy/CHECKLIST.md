# Checklist do deploy · 27/09, das 9h às 10h45

Ordem para colocar a demo no ar e congelar antes do pitch. Nada aqui foi executado na madrugada; o estado marcado como "conferido" foi lido com `gcloud`/`bq` (só metadados) em 27/09 às 01h. A configuração `default` do gcloud é de outro cliente: **tudo com `CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares`**. Identidade e papéis: `docs/12-identidade-e-seguranca.md`. Custo: `docs/11-finops.md`.

Dois caminhos para o mesmo resultado (mesma imagem, mesmos flags, mesma service account):

- **A · `./deploy/deploy.sh`** (caminho do dia): roda com as credenciais do Maná, que tem os papéis conferidos (`run.admin`, `cloudbuild.builds.editor`, `artifactregistry.writer`, `iam.serviceAccountUser` na `squad-agent-sa`).
- **B · CI `deploy/cloudbuild.yaml`** (test → build → push → deploy): `gcloud builds submit --config deploy/cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD),_MIN_INSTANCES=1 .`. O passo `deploy` roda com a service account de build do projeto, cujos papéis de implantação **não foram conferidos**; se ele falhar por permissão, volte para A sem perder nada (a imagem já estará em `agentes`). Gatilho por push fica para depois do evento: exige conectar o repositório GitHub ao Cloud Build; quando existir, com "Exigir aprovação" ligado.

| Hora | Passo | Comando / o que conferir | Estado |
|---|---|---|---|
| 9h00 | 0. Conta certa | `export CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares` · `gcloud config list` deve mostrar `account = mana.gsoares@gmail.com`, `project = batalha-time-05-xew3` · ADC: `gcloud auth application-default print-access-token >/dev/null && echo ok` | configuração `mana-gsoares` existe (conferido) |
| 9h02 | 1. APIs | `gcloud services list --enabled --filter="config.name:(run OR cloudbuild OR artifactregistry OR aiplatform OR bigquery OR secretmanager OR logging)"` · se faltar alguma: `gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com` | **já ligadas** (conferido): run, cloudbuild, artifactregistry, aiplatform, bigquery, secretmanager, logging |
| 9h03 | 2. Repositório de imagens | `gcloud artifacts repositories list --location=us-central1` deve listar `agentes` · se não: `gcloud artifacts repositories create agentes --repository-format=docker --location=us-central1` | **existe** (conferido; a imagem `cabe-no-bolso:smoke-a27a076` já está lá; há também `batalha-agentes`, não usar) |
| 9h05 | 3. Identidade de runtime | O serviço roda como **`squad-agent-sa@batalha-time-05-xew3.iam.gserviceaccount.com`** (`deploy.sh` e `cloudbuild.yaml` passam `--service-account`). Conferir: `gcloud projects get-iam-policy batalha-time-05-xew3 --flatten="bindings[].members" --filter="bindings.members:squad-agent-sa" --format="value(bindings.role)"` deve listar `roles/aiplatform.user` (Gemini via Vertex), `bigquery.jobUser` (só se `DADOS=bigquery`), `logging.logWriter`. **Não usar a SA padrão do Compute** (`602056186697-compute@...`): ela não tem `aiplatform.user` e o modelo dá 403. Zero chaves JSON: nada para baixar nem colar | **conferido em 01h**: `aiplatform.user`, `bigquery.admin`, `bigquery.jobUser`, `logging.logWriter`, `monitoring.metricWriter`, `secretmanager.secretAccessor`, `run.invoker` |
| 9h08 | 4. Código verde | `cd agent && uv run --frozen pytest -q` (todos verdes; nenhum chama o modelo) · `git status` limpo no commit que vai ao ar · opcional (gasta ~19 chamadas): `uv run python evals/rodar.py --limite-chamadas 25` | 27/09 02h20: 136 verdes (suíte inteira, inclui modo gi, checagens, validador offline e verificação final) |
| 9h12 | 5. Build local (só se houver daemon) | `docker info >/dev/null 2>&1 && docker build -t cabe-no-bolso . && docker run --rm -p 8080:8080 -e MODO_CONVERSA=sem_llm cabe-no-bolso` · em outro terminal: `curl -s localhost:8080/api/saude` → `{"ok": true, "dados": "csv", ...}` · abrir `http://localhost:8080/` | daemon **ausente** às 01h; se continuar ausente, pular (o Cloud Build faz a imagem) |
| 9h15 | 6. Deploy | **A:** `./deploy/deploy.sh` (da raiz). Faz: `gcloud builds submit` (imagem com contexto na raiz; `.gcloudignore` decide o que sobe) → `gcloud run deploy cabe-no-bolso --service-account=squad-agent-sa@... --min-instances=1 --max-instances=1 --session-affinity --concurrency=40 --cpu=1 --memory=1Gi --timeout=120 --cpu-boost` → imprime a URL, faz `curl /api/saude`, gera `deploy/qr-cabe-no-bolso.png` e mostra o comando de rollback. Variáveis: `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, `GOOGLE_CLOUD_LOCATION=global`, `GOOGLE_CLOUD_PROJECT`, `DADOS=csv`, `BIGQUERY_TABELA`, `MODELO=gemini-3.8-flash`, `MODELO_VALIDADOR` (= `MODELO`), `MODO_CONVERSA` (padrão lido de `agent/.env.example`), `RATE_LIMIT_POR_MINUTO=120`, `MAX_CONCORRENCIA=40`, `RAIZ=/app`. Sobrepor por env: `MODO_CONVERSA=sem_llm ./deploy/deploy.sh`; `MIN_INSTANCES=0` fora da demo. **B (CI):** `gcloud builds submit --config deploy/cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD),_MIN_INSTANCES=1 .` (roda os testes antes de construir). Tempo esperado: 5 a 8 min | não executado |
| 9h25 | 7. Fumaça na API | `URL=$(gcloud run services describe cabe-no-bolso --region us-central1 --format='value(status.url)')` · `curl -s $URL/api/saude` → `ok: true`, `dados: csv`, `modelo: gemini-3.8-flash`, `modo` ≠ `sem_llm` · `curl -s $URL/api/personas` lista Ana e Bruno · `curl -s -X POST $URL/api/sessao -H 'content-type: application/json' -d '{"cliente_id":"3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b","anomes":202509}'` devolve `sessao_id` e a fatura `361995` · `curl -sI $URL/docs` → 404 (OpenAPI desligado) · logs: `gcloud run services logs read cabe-no-bolso --region us-central1 --limit 50` (sem PII, sem erro 5xx, sem 403 do Vertex) | |
| 9h28 | 7b. FinOps no ar | `CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares ./deploy/metricas.sh` (uma vez; papel `logging.configWriter`) cria as métricas `logging/user/cabe_custo_usd`, `cabe_latencia_ms`, `cabe_mensagens_seguras`, `cabe_tokens`, `cabe_regeneracoes`, `cabe_validador_reprovou` a partir do log JSON por turno do servidor · conferir no Logs Explorer: `jsonPayload.evento="turno" AND resource.labels.service_name="cabe-no-bolso"` (linhas com `papel`, `custo_usd`, `latencia_ms`, sem PII) · `GET $URL/api/painel/<sessao_id>` traz `finops.custo_estimado` em USD com `preco_fonte` · orçamento com alertas 50/80/100%: `./deploy/orcamento.sh --mostrar` (executar exige papel de billing) | não executado |
| 9h30 | 8. Fumaça no celular | Abrir a URL no celular (Wi-Fi do evento e 4G): Ana → pagar o mínimo → permitir → ver opções → cobertura curta 7 dias R$ 7,98 → confirmar → avançar 3 meses → "como cheguei aqui" (0 números sem origem; rodapé de simulação em todas as telas). Repetir com Bruno (consignado 10× R$ 110,51). Conferir a pílula "API · dados csv" e a aba FinOps (chamadas, tokens, p50/p95). Se o modelo falhar, a pílula muda para `sem_llm` sem erro na tela | |
| 9h45 | 9. QR | `uv run --project agent python deploy/gerar_qr.py $URL` (QR em ASCII no terminal + `deploy/qr-cabe-no-bolso.png`) · variante direto na persona: `--persona ana` · colar o PNG no slide do pitch e imprimir um cartão para a mesa da banca · testar o QR com dois celulares | |
| 10h00 | 10. Segunda rodada ou rollback | Qualquer redeploy **zera as sessões em memória** (sessão = 1 instância). Se houver correção, redeployar até 10h30 e refazer os passos 7–9 (a URL não muda; o QR continua válido). Se a revisão nova estiver pior que a anterior, **rollback** (seção abaixo) em vez de corrigir às pressas | |
| 10h45 | 11. Congelar | Não tocar no serviço: sem `gcloud run deploy`, sem `services update`, sem trocar env, sem rollback. Deixar `deploy/qr-cabe-no-bolso.png` e a URL no README (§11) e no slide. Uma pessoa com o laptop pronto para o plano B | |
| pitch | 12. Plano B, em ordem | (a) modo sem modelo por env: `gcloud run services update cabe-no-bolso --region us-central1 --update-env-vars MODO_CONVERSA=sem_llm` (cria revisão nova: sessões zeram; só antes de 10h45) · (b) demo estática com respostas gravadas no laptop: `python3 -m http.server 8765 --directory demo` e abrir `http://localhost:8765/?mock=1&persona=ana` (espelhar a tela) · (c) API local por túnel: `cd agent && MODO_CONVERSA=sem_llm uv run uvicorn server.main:app --port 8080` · (d) vídeo de 60 s da jornada | |
| depois | 13. Parar de cobrar | `gcloud run services update cabe-no-bolso --region us-central1 --min-instances=0` (escala a zero fora da demo; `config/finops.yaml : min_instances_fora_da_demo`) · conferir o consumo em Cloud Billing | |

## Rollback

Cada `gcloud run deploy` cria uma revisão e manda 100% do tráfego para ela; as anteriores ficam guardadas. Voltar é mover o tráfego, sem rebuild:

```bash
gcloud run revisions list --service cabe-no-bolso --region us-central1          # nome, data e imagem de cada revisão
gcloud run services update-traffic cabe-no-bolso --region us-central1 \
  --to-revisions=cabe-no-bolso-00001-abc=100                                     # 100% para a revisão anterior (leva segundos)
gcloud run services update-traffic cabe-no-bolso --region us-central1 --to-latest   # desfazer: de volta à mais nova
```

O rollback também **zera as sessões em memória** (a instância da revisão anterior sobe do zero): só antes de 10h45, e refazer os passos 7 e 8. Como a URL do serviço não muda, o QR continua válido. Se o problema for só o modelo (403, 404, cota), prefira o plano B (a) (`MODO_CONVERSA=sem_llm`), que mantém a jornada inteira.

## Opcional: analytics do agente no BigQuery (não ligar na demo)

O `App` do agente aceita o `BigQueryAgentAnalyticsPlugin` do ADK (padrão trazido de guiwatanabe/iai-cabe-no-bolso): cada evento do agente e do validador vira uma linha no BigQuery (custo por cliente, cenário e dia com SQL; `docs/11-finops.md` §4). Só entra quando a variável `BQ_ANALYTICS_DATASET` existe; o dataset **não foi criado** na madrugada. Se o time quiser depois do pitch:

```bash
export CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares
bq mk --dataset --location=us-central1 batalha-time-05-xew3:agent_logs      # uma vez; a SA de runtime precisa de bigquery.dataEditor + jobUser nele
BQ_ANALYTICS_DATASET=agent_logs ./deploy/deploy.sh                          # a variável está comentada no deploy.sh; o Dockerfile precisa do extra bigquery (uv sync --extra bigquery)
```

Sem a dependência (`google.api_core`) o agente registra um aviso e sobe sem o plugin; nada muda para a banca.

## Opcional: Agent Engine

`deploy/deploy_agent_engine.sh` publica só o agente (`agent/cabe_no_bolso/`) na Agent Platform com `adk deploy agent_engine` (flags conferidas no `--help` do ADK 2.10.0). Não é o caminho da demo e não substitui o Cloud Run; serve para mostrar o agente na console ou como último recurso. A service account do Reasoning Engine precisa de `roles/aiplatform.user`. Não executar antes do passo 11.

## Se algo falhar

| Sintoma | Causa provável | Ação |
|---|---|---|
| `gcloud builds submit` nega permissão | conta errada (configuração `default`) ou Cloud Build sem acesso ao repositório `agentes` | `export CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares`; conferir `gcloud config list` |
| passo `deploy` do `cloudbuild.yaml` falha com `permission denied` ou `iam.serviceaccounts.actAs` | SA de build sem `run.admin` ou sem `iam.serviceAccountUser` na `squad-agent-sa` | usar o caminho A (`./deploy/deploy.sh`, credenciais do Maná); a imagem já foi publicada pelo passo `push` |
| `gcloud run deploy` recusa `--service-account` | conta sem `iam.serviceAccountUser` na `squad-agent-sa` | conferido para o Maná em 01h; se for outra pessoa, o Maná roda o deploy |
| `/api/saude` responde `modo: sem_llm` com o modelo configurado, ou 403 do Vertex nos logs | serviço rodando com a SA padrão do Compute (sem `aiplatform.user`), ou modelo fora de `global` | `gcloud run services describe cabe-no-bolso --region us-central1 --format='value(spec.template.spec.serviceAccountName)'` deve ser `squad-agent-sa@...`; conferir `GOOGLE_CLOUD_LOCATION=global` (`--format=yaml \| grep -A2 GOOGLE_CLOUD_LOCATION`) |
| 404 do modelo nos logs | `gemini-3.8-flash` só responde em `global` (us-central1 dá 404) | idem; reserva `MODELO=gemini-2.5-flash` |
| 429 na banca | rate limit por IP (todos no mesmo Wi-Fi) | já em 120/min; se precisar, `RATE_LIMIT_POR_MINUTO=300 ./deploy/deploy.sh` antes de 10h45 |
| dados indisponíveis | `DADOS=bigquery` sem papel de BigQuery na SA | voltar para `DADOS=csv` (padrão) |
| revisão nova pior que a anterior | correção de última hora | rollback (seção acima) até 10h45 |
| sessão expirada no meio da demo | redeploy ou rollback depois de 10h45, ou TTL de 2 h | não redeployar; botão "Reiniciar" na demo |
