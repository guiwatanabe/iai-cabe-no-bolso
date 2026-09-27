"""ADK guardrails. Argument schemas are enforced by the MCP tools; these are policy."""

import re

from google.adk.models import LlmResponse
from google.genai import types

from .grounding import Answer

MAX_TOOL_CALLS_PER_TURN = 8

# Cheap first line of defence; swap for a classifier model call if needed.
_BLOCKED = re.compile(r"ignore (all |the )?(previous|above) instructions|system prompt|drop\s+table|delete\s+from", re.I)

_REFUSAL = Answer(status="out_of_scope", text="Não posso ajudar com esse pedido.").model_dump_json()


def block_unsafe_input(callback_context, llm_request):
    """before_model: short-circuit the LLM call when the user's message is off-limits."""
    last = llm_request.contents[-1] if llm_request.contents else None
    if not last or last.role != "user":
        return None
    text = " ".join(p.text or "" for p in last.parts or [])
    if _BLOCKED.search(text):
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=_REFUSAL)])
        )
    return None


def limit_tool_calls(tool, args, tool_context):
    """before_tool: cap BigQuery calls per turn (cost + runaway-loop guard)."""
    calls = tool_context.state.get("temp:tool_calls", 0) + 1
    tool_context.state["temp:tool_calls"] = calls
    if calls > MAX_TOOL_CALLS_PER_TURN:
        return {"error": f"Tool budget of {MAX_TOOL_CALLS_PER_TURN} calls exceeded. Answer with the data you have."}
    return None

