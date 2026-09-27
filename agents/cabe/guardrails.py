"""ADK guardrails. Argument schemas are enforced by the MCP tools; these are policy.

Code checks follow the PRD notes (Notas p.3): what has a right or wrong answer is checked here,
before the validator LLM. A failed check turns the answer into the PRD safe message.
"""

import json
import logging
import re

from google.adk.models import LlmResponse
from google.genai import types
from pydantic import ValidationError

from . import validador
from .grounding import Resposta, format_fact, render_answer

log = logging.getLogger(__name__)

MAX_TOOL_CALLS_PER_TURN = 8
MAX_INSIGHT_CHARS = 160
MAX_MENSAGENS = 3
CONTEXTO_TOOL = "contexto_fatura"
_ACOES_COM_CONSENTIMENTO = {"mostrar_oferta", "abrir_resumo_contrato"}
_ACOES_DE_OFERTA = {"mostrar_oferta", "abrir_resumo_contrato", "registrar_permissao_ampliacao"}
# Notas p.3: these failures regenerate once; offer and consent failures go straight to the safe message.
_REGENERA = {
    "json",
    "formato insight",
    "formato conversa",
    "fato desconhecido",
    "tamanho insight",
    "tamanho conversa",
    "termo proibido",
}
_TERMOS_PROIBIDOS = (
    "score",
    "análise de risco",
    "negativa",
    "limite recusado",
    "grupo do cliente",
    "escorregão",
    "rolando a fatura",
    "pedalada",
    "da mais barata para a mais cara",
)

_BLOCKED = re.compile(
    r"ignore (all |the )?(previous|above) instructions|system prompt|drop\s+table|delete\s+from", re.IGNORECASE
)

# PRD example C16 (attempt to change the rules).
_REFUSAL = {
    "mensagens": [
        (
            "Não consigo mudar as condições nem liberar crédito por aqui. Posso te mostrar as formas de pagar a "
            "fatura, ou te passar para alguém da equipe."
        )
    ],
    "acao": "nenhuma",
    "oferta_id": None,
    "numeros_citados": [],
}


def block_unsafe_input(callback_context, llm_request):
    """before_model: short-circuit the LLM call when the user's message is off-limits."""
    last = llm_request.contents[-1] if llm_request.contents else None
    if not last or last.role != "user":
        return None
    text = " ".join(p.text or "" for p in last.parts or [])
    if _BLOCKED.search(text):
        return LlmResponse(content=_as_content(_REFUSAL))
    return None


def limit_tool_calls(tool, args, tool_context):
    """before_tool: cap tool calls per turn (cost + runaway-loop guard)."""
    calls = tool_context.state.get("temp:tool_calls", 0) + 1
    tool_context.state["temp:tool_calls"] = calls
    if calls > MAX_TOOL_CALLS_PER_TURN:
        return {"error": f"Tool budget of {MAX_TOOL_CALLS_PER_TURN} calls exceeded. Answer with the data you have."}
    return None


def before_tool(tool, args, tool_context):
    """before_tool: budget, then session identity and state injected over whatever the model sent."""
    if blocked := limit_tool_calls(tool, args, tool_context):
        return blocked
    state = tool_context.state
    estado = state.get("estado") or {}
    if tool.name == CONTEXTO_TOOL:
        args["cliente_id"] = state.get("cliente_id", "")
        args["estado"] = estado
    elif tool.name == "explicar_fatura":
        if not estado.get("consentimento"):
            return {"error": "Sem consentimento do cliente: não detalhe a fatura. Mostre só as formas de pagar."}
        args["cliente_id"] = state.get("cliente_id", "")
    return None


def finalize_answer(callback_context, llm_response):
    """after_model: force `contexto_fatura`, render [[fN]], run the code checks, then the validator hook."""
    content = llm_response.content
    if llm_response.partial or not content or not content.parts or any(p.function_call for p in content.parts):
        return None
    raw = "".join(p.text for p in content.parts if p.text and not p.thought)
    if not raw.strip():
        return None
    state = callback_context.state
    if not state.get("temp:contexto_ok"):
        if not state.get("temp:contexto_forcado"):
            # A final answer without the context is never shown: call the tool once instead.
            state["temp:contexto_forcado"] = True
            return _chamar_contexto()
        out = mensagem_segura(state)
    else:
        out = checar(state, raw)
        if isinstance(out, dict):
            out = validar_resposta(callback_context, out)
        elif out in _REGENERA:
            out = _regenerar_ou_segura(state, f"A resposta falhou na checagem automática ({out}). Corrija e responda.")
        else:
            out = mensagem_segura(state)
        if out is None:
            return _chamar_contexto()
    llm_response.content = _as_content(out)
    return llm_response


def checar(state, raw: str) -> dict | str:
    """Parse, render and check a final answer. A str is the failed check's name (Notas p.3)."""
    modo = _modo(state)
    ofertas = (state.get("contexto") or {}).get("ofertas_liberadas") or []
    if state.get("gatilho") == "pagar_outro_valor" and not ofertas:
        return _vazia(modo)
    try:
        resposta = Resposta.model_validate_json(raw)
    except ValidationError:
        return _reprova("json")
    if modo == "insight":
        if resposta.texto is None or resposta.botao_primario is None:
            return _reprova("formato insight")
        resposta = Resposta(**resposta.model_dump(include={"texto", "botao_primario", "botao_secundario"}))
    else:
        if resposta.mensagens is None or resposta.acao is None:
            return _reprova("formato conversa")
        resposta = Resposta(**resposta.model_dump(include={"mensagens", "acao", "oferta_id"}))
    out = render_answer(resposta, state.get("facts", {}))
    if out is None:
        return _reprova("fato desconhecido")
    # Notas p.3: oferta_id must exist in ofertas_liberadas (null when the list is empty), else safe message.
    if out["oferta_id"] is not None and out["oferta_id"] not in {o.get("id") for o in ofertas}:
        return _reprova("oferta")
    if out["acao"] in _ACOES_DE_OFERTA and out["oferta_id"] is None:
        return _reprova("oferta")
    if not (state.get("estado") or {}).get("consentimento") and out["acao"] in _ACOES_COM_CONSENTIMENTO:
        return _reprova("consentimento")
    if modo == "insight" and len(out["texto"]) > MAX_INSIGHT_CHARS:
        return _reprova("tamanho insight")
    if modo != "insight" and len(out["mensagens"]) > MAX_MENSAGENS:
        return _reprova("tamanho conversa")
    out = _projeta(out, modo)
    texto = json.dumps(out, ensure_ascii=False).lower()
    if any(termo in texto for termo in _TERMOS_PROIBIDOS):
        return _reprova("termo proibido")
    return out


def validar_resposta(callback_context, resposta: dict) -> dict | None:
    """Validator LLM (PRD R1–R20) on the rendered answer that passed the code checks.

    Approved -> the answer; R8 -> a person, no retry; first rejection -> None (the model regenerates with the
    validator's guidance); second rejection -> safe message.
    """
    state = callback_context.state
    contexto = {"modo": _modo(state), **contexto_validador(state)}
    veredito = validador.validar(contexto, _historico(callback_context), resposta)
    if veredito.aprovado:
        return resposta
    regras = sorted({v.regra for v in veredito.violacoes})
    log.warning("validator rejected: %s", regras or ["?"])
    if "R8" in regras:
        return mensagem_segura(state, transferir=True)
    return _regenerar_ou_segura(state, validador.orientacao(veredito))


def contexto_validador(state) -> dict:
    """What the validator and the safe message see: the tool's contexto plus every rendered value of the session."""
    facts, ids = state.get("facts", {}), state.get("fatos_contexto") or {}

    def valor(key):
        fact = facts.get(ids.get(key))
        return format_fact(fact) if fact else None

    return {
        **(state.get("contexto") or {}),
        "gatilho": state.get("gatilho") or "pergunta_cliente",
        "fatura": {"valor": valor("fatura_valor"), "vencimento": valor("fatura_vencimento")},
        "valores": {f["label"]: format_fact(f) for f in facts.values()},
    }


def mensagem_segura(state, transferir: bool = False) -> dict:
    """PRD fixed text (validador.mensagem_segura) with the bill facts of the latest `contexto_fatura`."""
    return validador.mensagem_segura(contexto_validador(state), _modo(state), transferir)


def _regenerar_ou_segura(state, orientacao: str) -> dict | None:
    """One regeneration per invocation (Notas): None asks for it; a second failure gets the safe message."""
    if state.get("temp:regenerado"):
        return mensagem_segura(state)
    state["temp:regenerado"] = True
    state["temp:orientacao"] = orientacao  # delivered to the model with the next contexto_fatura result
    return None


def _chamar_contexto() -> LlmResponse:
    call = types.FunctionCall(name=CONTEXTO_TOOL, args={})
    # Gemini 3 rejects function calls without a thought signature (400 INVALID_ARGUMENT); this call is injected by
    # code, not generated by the model, so it carries the documented dummy signature that skips the validation.
    part = types.Part(function_call=call, thought_signature=b"skip_thought_signature_validator")
    return LlmResponse(content=types.Content(role="model", parts=[part]))


def _historico(callback_context) -> list[dict]:
    session = getattr(callback_context, "session", None)
    historico = []
    for event in session.events if session else []:
        parts = event.content.parts if event.content and event.content.parts else []
        texto = " ".join(p.text for p in parts if p.text and not p.thought)
        if texto:
            historico.append({"autor": event.author, "texto": texto})
    return historico


def _modo(state) -> str:
    return state.get("modo") or "conversa"


def _vazia(modo: str) -> dict:
    if modo == "insight":
        return {"texto": "", "botao_primario": None, "botao_secundario": None, "numeros_citados": []}
    return {"mensagens": [], "acao": "nenhuma", "oferta_id": None, "numeros_citados": []}


def _projeta(out: dict, modo: str) -> dict:
    keys = ("texto", "botao_primario", "botao_secundario") if modo == "insight" else ("mensagens", "acao", "oferta_id")
    return {k: out[k] for k in (*keys, "numeros_citados")}


def _reprova(regra: str) -> str:
    log.warning("code check failed: %s", regra)  # PRD: every rejection is recorded with its rule
    return regra


def _as_content(out: dict) -> types.Content:
    return types.Content(role="model", parts=[types.Part(text=json.dumps(out, ensure_ascii=False))])
