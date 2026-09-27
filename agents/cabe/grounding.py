"""Grounded answers: the model references tool facts by id ([[f1]]); code renders the values.

Every value in the final answer is substituted from a registered tool fact, and an
unknown id rejects the whole answer, so the model cannot invent a value via a placeholder.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

MAX_ROWS_TO_MODEL = 50
_REF = re.compile(r"\[\[(f\d+)\]\]")

Acao = Literal[
    "nenhuma",
    "mostrar_formas_de_pagar",
    "mostrar_oferta",
    "abrir_resumo_contrato",
    "registrar_permissao_ampliacao",
    "mudar_vencimento",
    "revogar_consentimento",
    "transferir_humano",
    "devolver_ao_iai",
]


class Botao(BaseModel):
    rotulo: str
    acao: Literal["abrir_chat", "ver_formas_de_pagar", "nenhuma"]


# ADK takes one static output_schema, but the mode is only known at runtime (state `modo`), and a
# union (anyOf) is not reliably honoured by Gemini's response_schema. So one flat model carries both
# modes' fields as optional, and guardrails validates the right subset per `modo` in code.
class Resposta(BaseModel):
    mensagens: list[str] | None = Field(None, description="modo conversa: até 3 mensagens curtas, valores como [[fN]]")
    acao: Acao | None = Field(None, description="modo conversa")
    oferta_id: str | None = Field(None, description="modo conversa: id de ofertas_liberadas, ou null")
    texto: str | None = Field(None, description="modo insight: até 160 caracteres, valores como [[fN]]")
    botao_primario: Botao | None = Field(None, description="modo insight")
    botao_secundario: Botao | None = Field(None, description="modo insight")


def register_facts(tool, args, tool_context, tool_response):
    """after_tool: give each fact a session-wide id, keep only structured data, cap rows.

    For `contexto_fatura`, also keep the latest `contexto` and its key -> fact id map in state (code checks, safe
    message, validator) and mark that the tool ran in this invocation (forced-call guard).
    """
    if not isinstance(tool_response, dict) or tool_response.get("isError"):
        return None
    data = tool_response.get("structuredContent")
    if not isinstance(data, dict):
        return None
    facts = dict(tool_context.state.get("facts", {}))
    visible, ids = {}, {}
    for key, fact in (data.get("facts") or {}).items():
        fid = ids[key] = f"f{len(facts) + 1}"
        facts[fid] = visible[fid] = fact
    tool_context.state["facts"] = facts
    if getattr(tool, "name", None) == "contexto_fatura":
        tool_context.state["contexto"] = data.get("contexto") or {}
        tool_context.state["fatos_contexto"] = ids  # tool key -> fid, e.g. {"fatura_valor": "f1"}
        tool_context.state["temp:contexto_ok"] = True
    rows = data.get("rows") or []
    return {**data, "facts": visible, "rows": rows[:MAX_ROWS_TO_MODEL], "rows_truncated": len(rows) > MAX_ROWS_TO_MODEL}


def render_answer(resposta: Resposta, facts: dict) -> dict | None:
    """Substitute [[fN]] in every text field; None if any id is unknown.

    Returns the answer as a dict plus `numeros_citados`: the rendered values, in order, deduplicated.
    """
    out = resposta.model_dump()
    texts = [out["texto"] or "", *(out["mensagens"] or [])]
    texts += [b["rotulo"] for b in (out["botao_primario"], out["botao_secundario"]) if b]
    if {ref for t in texts for ref in _REF.findall(t)} - facts.keys():
        return None
    cited: list[str] = []

    def sub(text: str) -> str:
        def one(m: re.Match) -> str:
            value = format_fact(facts[m.group(1)])
            if value not in cited:
                cited.append(value)
            return value

        return _REF.sub(one, text)

    if out["texto"] is not None:
        out["texto"] = sub(out["texto"])
    if out["mensagens"] is not None:
        out["mensagens"] = [sub(m) for m in out["mensagens"]]
    for key in ("botao_primario", "botao_secundario"):
        if out[key]:
            out[key]["rotulo"] = sub(out[key]["rotulo"])
    out["numeros_citados"] = cited
    return out


def format_fact(fact: dict) -> str:
    value, unit = fact.get("value"), fact.get("unit")
    if isinstance(value, str):
        return value
    if unit == "BRL":
        value = round(value, 2)
        return "R$ " + _br(value, 0 if value == int(value) else 2)
    if unit == "pct":
        return _br(value, 2).rstrip("0").rstrip(",") + "%"
    if unit == "dia":
        return f"dia {value}"
    if unit == "dias":
        return f"{value} dia" + ("" if value == 1 else "s")
    return _br(value, 0 if isinstance(value, int) else 2)


def _br(n: float, decimals: int) -> str:
    return f"{n:,.{decimals}f}".translate(str.maketrans(",.", ".,"))
