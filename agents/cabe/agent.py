import os
import sys
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.plugins import ReflectAndRetryToolPlugin
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

from . import grounding, guardrails

REPO_ROOT = Path(__file__).resolve().parents[2]

fatura_tools = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=REPO_ROOT,
            env=dict(os.environ),  # stdio only inherits a minimal env by default
        ),
        timeout=30,
    ),
    tool_filter=["contexto_fatura", "explicar_fatura"],
)

INSTRUCTION = (Path(__file__).parent / "instruction.md").read_text(encoding="utf-8")


def instruction(ctx: ReadonlyContext) -> str:
    """InstructionProvider: the prompt has JSON braces, so it must bypass ADK's {state} templating."""
    modo = ctx.state.get("modo") or "conversa"
    gatilho = ctx.state.get("gatilho") or "pergunta_cliente"
    return f"{INSTRUCTION}\n<sessao>\nmodo: {modo}\ngatilho: {gatilho}\n</sessao>\n"


# Gemini 3.x thinks by default: 1.7k thought tokens for a 180-token answer, ~28 s per turn with the validator
# (measured 27/09). The numbers come from the tools, so the minimum budget is enough; empty keeps the model default.
_BUDGET = os.getenv("THINKING_BUDGET", "0").strip()
THINKING = types.ThinkingConfig(thinking_budget=int(_BUDGET)) if _BUDGET else None


root_agent = LlmAgent(
    name="cabe",
    model=Gemini(
        model=os.getenv("MODEL", "gemini-3.5-flash"),
        # Agent Engine sets GOOGLE_CLOUD_LOCATION to its own region; the model is served from global.
        client_kwargs={"location": "global"},
        # Retries 408/429/5xx; short cap so a demo request never hangs long.
        retry_options=types.HttpRetryOptions(attempts=3, max_delay=8),
    ),
    instruction=instruction,
    output_schema=grounding.Resposta,
    tools=[fatura_tools],
    generate_content_config=types.GenerateContentConfig(temperature=0.1, thinking_config=THINKING),
    before_model_callback=guardrails.block_unsafe_input,
    before_tool_callback=guardrails.before_tool,
    after_tool_callback=grounding.register_facts,
    after_model_callback=guardrails.finalize_answer,
)

plugins = [ReflectAndRetryToolPlugin(max_retries=2)]
if dataset := os.getenv("BQ_ANALYTICS_DATASET"):
    from google.adk.plugins.bigquery_agent_analytics_plugin import BigQueryAgentAnalyticsPlugin

    plugins.append(BigQueryAgentAnalyticsPlugin(project_id=os.environ["GOOGLE_CLOUD_PROJECT"], dataset_id=dataset))

app = App(name="cabe", root_agent=root_agent, plugins=plugins)
