FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY agents ./agents
COPY mcp_server ./mcp_server

RUN useradd --system app
USER app
ENV PATH="/app/.venv/bin:$PATH"

CMD ["sh", "-c", "exec adk api_server --host 0.0.0.0 --port ${PORT:-8080} agents"]
