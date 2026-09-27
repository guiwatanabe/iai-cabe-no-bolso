# cabe-no-bolso

Agente em pt-BR (Google ADK + Gemini no Vertex AI) que conversa sobre a fatura do cartão: mostra se ela cabe
no mês e oferece só o que o serviço de crédito liberou e cabe na folga (Spec do Cabe no Bolso, 27/09).
Os fatos do cliente vêm da camada analítica no BigQuery (`sql/`), servidos por um MCP server somente leitura.

O agente roda no Vertex AI Agent Engine (agente, config e sessões gerenciadas); o serviço no Cloud Run
é só um proxy com a API da demo, que cuida da identidade: `user_id` é um cookie aleatório emitido pelo proxy
e `cliente_id` vem de uma lista fixa de personas da demo.

- `agents/cabe/`: agente ADK. `instruction.md` é o prompt da Spec; o modelo cita fatos como `[[f1]]` e o código
  renderiza os valores; checagens em código e o validador (regras R1–R20) rodam antes da resposta sair.
  `.agent_engine_config.json` (env vars, instâncias, concorrência) e `requirements.txt` definem o deploy no Agent Engine
- `mcp_server/`: MCP server (stdio) com as ferramentas `contexto_fatura` e `explicar_fatura`. `core/` calcula
  mínimo, juros, ofertas e custos (taxas só de `taxas.yaml`); `dados.py` lê uma linha do gold por cliente
  (`CABE_DADOS=fixtures` lê `tests/fixtures/` em vez do BigQuery)
- `sql/`: camada analítica (silver/gold) e checagens; o que mudou e por quê em `sql/docs/mudancas.md`
- `proxy/`: FastAPI com `POST /sessao`, `GET /sessao/{id}`, `/run` e `/run_sse`, repassados ao Agent Engine
- `scripts/deploy.sh`: deploy completo a partir do notebook
- `tests/`: unitários de cada camada, um fim a fim local (Runner do ADK + MCP server real + modelo roteirizado,
  sem Gemini nem BigQuery) e os pins de `requirements.txt` contra o `uv.lock`
- `.github/workflows/test.yml`: ruff + pytest a cada push em `main` e em PRs (sem credenciais do GCP)

Python 3.11 em todo lugar (`.python-version`), igual ao container do Agent Engine.

## Rodar local

```bash
cp .env.example .env               # na raiz: o adk web acha subindo pastas, e o deploy não lê nem envia
gcloud auth application-default login
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
CABE_DADOS=fixtures uv run adk web agents   # agente local sobre as fixtures (chama o Gemini), UI em http://localhost:8000

# proxy local apontando para o agente já publicado
AGENT_ENGINE=projects/PROJ/locations/us-central1/reasoningEngines/ID uv run uvicorn proxy.app:app --port 8080
```

## Deploy

O projeto do hackathon (`batalha-time-05-xew3`) é do organizador: o time não cria service accounts,
permissões nem conexão com o GitHub, então o CI só testa. O deploy roda no notebook, com as suas credenciais do gcloud:

```bash
scripts/deploy.sh
```

Só publica um checkout limpo igual a `origin/main`. Ele roda ruff e pytest, cria o dataset `agent_logs`
(us-central1) se faltar, publica o agente no Agent Engine (cria a instância `cabe` na primeira vez, via `scripts/agent_engine.py`), gera a imagem do proxy no Cloud Build
(repositório `agentes`) e publica no Cloud Run. Agente e proxy rodam como `squad-agent-sa`, já criada no projeto.

- Limites de custo: Agent Engine e Cloud Run com mín. 1 e máx. 3 instâncias (sem cold start na demo).
  Não dá para criar alerta de orçamento no projeto. Depois do pitch, publique de novo com mín. 0.
- Sessões expiram em 1 dia.
- Um `agents/cabe/.env` substituiria as env vars do config e iria junto no deploy; o script recusa rodar se ele existir.

## Exemplo de chamada

```bash
URL=$(gcloud run services describe iai-cabe-no-bolso --region us-central1 --format='value(status.url)')

# o proxy devolve o cookie uid; -c/-b guardam e reenviam
# uma sessão = um gatilho num modo; consentimento dado ou revogado abre uma sessão nova
curl -c jar -X POST "$URL/sessao" -H 'Content-Type: application/json' -d '{
  "cliente_id": "3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b", "modo": "conversa", "gatilho": "pergunta_cliente",
  "consentimento": true, "liberacao": {"cobertura": true, "consignado": true, "prestamista": false}
}'     # -> {"sessao_id": "..."}

curl -b jar -X POST "$URL/run" -H 'Content-Type: application/json' -d '{
  "sessao_id": "SESSAO_ID",
  "new_message": {"role": "user", "parts": [{"text": "Consigo pagar minha fatura?"}]}
}'
```

## Licença

MIT
