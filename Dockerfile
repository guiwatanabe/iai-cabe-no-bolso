# Cabe no Bolso · API + demo num único serviço (Cloud Run). Contexto de build = raiz do repo.
# A imagem precisa de config/ (taxas), data/ (CSV de reserva e personas), demo/ (estático) e agent/ (código).
# Build local (opcional): docker build -t cabe-no-bolso . && docker run --rm -p 8080:8080 -e MODO_CONVERSA=sem_llm cabe-no-bolso

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3.11 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/agent/.venv

# uv (gerenciador do projeto); versão fixa para builds reproduzíveis
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

# dependências primeiro (cache de camada); o projeto é "virtual" (package=false), então só as libs
WORKDIR /app/agent
COPY agent/pyproject.toml agent/uv.lock agent/.python-version ./
RUN uv sync --frozen --no-dev

# o que o serviço lê em tempo de execução, com caminhos resolvidos a partir de RAIZ=/app
COPY config/ /app/config/
COPY data/extrato_sintetico.csv.gz data/README.md /app/data/
COPY data/personas/ /app/data/personas/
COPY demo/ /app/demo/
COPY agent/ /app/agent/

# usuário não root
RUN groupadd --system app && useradd --system --gid app --home /app --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

ENV RAIZ=/app \
    DADOS=csv \
    MODELO=gemini-3.8-flash \
    PORT=8080 \
    PATH="/app/agent/.venv/bin:$PATH"

EXPOSE 8080
# um worker: sessões em memória (InMemorySessionService) + --session-affinity no Cloud Run
CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-access-log --proxy-headers --forwarded-allow-ips='*'"]
