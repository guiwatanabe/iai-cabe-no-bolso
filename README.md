# cabe-no-bolso

Agente em pt-BR (Google ADK + Gemini no Vertex AI) que responde perguntas usando apenas dados do BigQuery,
servidos por um MCP server com ferramentas curadas e somente leitura.

- `agents/cabe/`: agente ADK com guardrails e grounding (o modelo cita fatos como `[[f1]]`, o código renderiza os valores)
- `mcp_server/`: MCP server (stdio) com queries parametrizadas e limite de bytes faturados
- `tests/`: testes do grounding

## Rodar local

```bash
cp agents/cabe/.env.example agents/cabe/.env   # ajuste GOOGLE_CLOUD_PROJECT
gcloud auth application-default login
uv sync
uv run pytest
uv run adk web agents          # UI em http://localhost:8000
```

## Deploy

Push em `main` dispara um build no Cloud Build (`cloudbuild.yaml`): pytest → imagem → Artifact Registry → Cloud Run.
Cada build precisa de aprovação em Cloud Build > History.

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
