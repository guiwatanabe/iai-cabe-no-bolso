# cabe-no-bolso

Agente em pt-BR (Google ADK + Gemini no Vertex AI) que responde perguntas usando apenas dados do BigQuery,
servidos por um MCP server com ferramentas curadas e somente leitura.

O agente roda no Vertex AI Agent Engine (agente, config e sessões gerenciadas); o serviço no Cloud Run
é só um proxy com as mesmas rotas do `adk api_server`.

- `agents/cabe/`: agente ADK com guardrails e grounding (o modelo cita fatos como `[[f1]]`, o código renderiza os valores).
  `.agent_engine_config.json` e `requirements.txt` definem o deploy no Agent Engine
- `mcp_server/`: MCP server (stdio) com queries parametrizadas e limite de bytes faturados; vai junto com o agente
- `proxy/`: FastAPI que repassa sessões e `/run` / `/run_sse` para o Agent Engine
- `tests/`: testes do grounding e do proxy

## Rodar local

```bash
cp agents/cabe/.env.example agents/cabe/.env   # ajuste GOOGLE_CLOUD_PROJECT
gcloud auth application-default login
uv sync
uv run pytest
uv run adk web agents          # agente local, UI em http://localhost:8000

# proxy local apontando para o agente já publicado
AGENT_ENGINE=projects/PROJ/locations/us-central1/reasoningEngines/ID uv run uvicorn proxy.app:app --port 8080
```

## Deploy

Push em `main` dispara um build no Cloud Build (`cloudbuild.yaml`): pytest → `adk deploy agent_engine`
(cria a instância `cabe` no primeiro build, via `scripts/agent_engine.py`) e imagem do proxy → Cloud Run
com `AGENT_ENGINE` apontando para a instância. Cada build precisa de aprovação em Cloud Build > History.

Env vars do agente ficam em `agents/cabe/.agent_engine_config.json`. Um `agents/cabe/.env` local substitui
todas elas num deploy manual, então publique pelo CI.

Setup único do GCP (idempotente): `scripts/bootstrap.sh`.

## Exemplo de chamada

```bash
URL=$(gcloud run services describe iai-cabe-no-bolso --region us-central1 --format='value(status.url)')

curl -X POST "$URL/apps/cabe/users/demo/sessions/s1" -H 'Content-Type: application/json' -d '{}'

curl -X POST "$URL/run" -H 'Content-Type: application/json' -d '{
  "app_name": "cabe", "user_id": "demo", "session_id": "s1",
  "new_message": {"role": "user", "parts": [{"text": "Qual foi o nome mais registrado na CA em 2000?"}]}
}'
```

## Licença

MIT
