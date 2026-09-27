"""Grounded answers: the model references tool facts by id ([[f1]]); code renders the values.

Every value in the final answer is substituted from a registered BigQuery fact, and an
unknown id rejects the whole answer, so the model cannot invent a value via a placeholder.
"""

import re
from typing import Literal

from google.genai import types
from pydantic import BaseModel, Field, ValidationError

MAX_ROWS_TO_MODEL = 50
_REF = re.compile(r"\[\[(f\d+)\]\]")


class Answer(BaseModel):
    status: Literal["answered", "no_data", "out_of_scope"]
    text: str = Field(
        description="Resposta em pt-BR. Todo valor vindo das ferramentas deve ser escrito como [[id]], "
        "ex.: [[f1]]. Nunca escreva números ou valores diretamente."
    )


UNVERIFIED = Answer(status="no_data", text="Não consegui verificar essa resposta com os dados disponíveis.")


def register_facts(tool, args, tool_context, tool_response):
    """after_tool: give each fact a session-wide id, keep only structured data, cap rows."""
    if not isinstance(tool_response, dict) or tool_response.get("isError"):
        return None
    data = tool_response.get("structuredContent")
    if not isinstance(data, dict):
        return None
    facts = dict(tool_context.state.get("facts", {}))
    visible = {}
    for fact in (data.get("facts") or {}).values():
        fid = f"f{len(facts) + 1}"
        facts[fid] = visible[fid] = fact
    tool_context.state["facts"] = facts
    rows = data.get("rows") or []
    return {**data, "facts": visible, "rows": rows[:MAX_ROWS_TO_MODEL], "rows_truncated": len(rows) > MAX_ROWS_TO_MODEL}


def render_answer(callback_context, llm_response):
    """after_model: validate [[id]] refs against registered facts and substitute formatted values."""
    content = llm_response.content
    if llm_response.partial or not content or not content.parts or any(p.function_call for p in content.parts):
        return None
    raw = "".join(p.text for p in content.parts if p.text and not p.thought)
    if not raw.strip():
        return None
    facts = callback_context.state.get("facts", {})
    try:
        answer = Answer.model_validate_json(raw)
    except ValidationError:
        answer = UNVERIFIED
    if set(_REF.findall(answer.text)) - facts.keys():
        answer = UNVERIFIED
    answer.text = _REF.sub(lambda m: format_fact(facts[m.group(1)]), answer.text)
    llm_response.content = types.Content(role="model", parts=[types.Part(text=answer.model_dump_json())])
    return llm_response


def format_fact(fact: dict) -> str:
    value, unit = fact.get("value"), fact.get("unit")
    if isinstance(value, str):
        return value
    if unit == "BRL":
        return "R$ " + _br(value, 2)
    if unit == "pct":
        return _br(value, 1) + "%"
    return _br(value, 0 if isinstance(value, int) else 2)


def _br(n: float, decimals: int) -> str:
    return f"{n:,.{decimals}f}".translate(str.maketrans(",.", ".,"))
