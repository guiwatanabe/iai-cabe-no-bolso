import os
import sys
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.plugins import ReflectAndRetryToolPlugin
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

from . import grounding, guardrails

REPO_ROOT = Path(__file__).resolve().parents[2]

bq_tools = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=REPO_ROOT,
            env=dict(os.environ),  # stdio only inherits a minimal env by default
        ),
        timeout=30,
    ),
    tool_filter=["top_names"],
)

root_agent = LlmAgent(
    name="cabe",
    model=Gemini(
        model=os.getenv("MODEL", "gemini-3.5-flash"),
        # Agent Engine sets GOOGLE_CLOUD_LOCATION to its own region; the model is served from global.
        client_kwargs={"location": "global"},
        # Retries 408/429/5xx; short cap so a demo request never hangs long.
        retry_options=types.HttpRetryOptions(attempts=3, max_delay=8),
    ),
    instruction=(
        "Você responde em pt-BR usando apenas dados retornados pelas ferramentas.\n"
        "- Cada ferramenta devolve `facts` com ids (f1, f2, ...). Para citar qualquer valor, "
        "escreva o id entre colchetes duplos, ex.: [[f1]]. Nunca escreva números ou valores diretamente.\n"
        "- Nunca calcule nada. Se o valor pedido não estiver em `facts`, diga que não tem esse dado.\n"
        "- status: answered se respondeu com fatos; no_data se a ferramenta voltou vazia ou falta o dado; "
        "out_of_scope se a pergunta não é sobre os dados disponíveis."
    ),
    output_schema=grounding.Answer,
    tools=[bq_tools],
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
    before_model_callback=guardrails.block_unsafe_input,
    before_tool_callback=guardrails.limit_tool_calls,
    after_tool_callback=grounding.register_facts,
    after_model_callback=grounding.render_answer,
)

plugins = [ReflectAndRetryToolPlugin(max_retries=2)]
if dataset := os.getenv("BQ_ANALYTICS_DATASET"):
    from google.adk.plugins.bigquery_agent_analytics_plugin import BigQueryAgentAnalyticsPlugin

    plugins.append(BigQueryAgentAnalyticsPlugin(project_id=os.environ["GOOGLE_CLOUD_PROJECT"], dataset_id=dataset))

app = App(name="cabe", root_agent=root_agent, plugins=plugins)
