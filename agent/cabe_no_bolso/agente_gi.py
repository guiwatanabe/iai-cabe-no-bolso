"""Agente no modo da Gi: um LlmAgent sem ferramentas de dados, com o prompt da Gi preenchido a cada turno.

O prompt de sistema é um InstructionProvider: lê state["gi_contexto_json"] (o contexto já calculado por cabe_core e
gravado pelo runtime antes da chamada) e preenche {{diretrizes_itau}}, {{contexto_json}} e {{exemplos}} em código.
Texto do cliente nunca entra no prompt de sistema: a mensagem do cliente vai no turno de usuário e o histórico fica
nos eventos da sessão do ADK. O modelo responde sempre em JSON (response_mime_type) no formato do prompt.

Mesmo nome do agente do modo tools (NOME): os dois runners compartilham o InMemorySessionService, e o ADK trata os
eventos como do próprio agente. Sem guardião de texto no after_model: no modo gi as checagens em código
(checagens.checar) e o validador cuidam da saída; o callback só registra FinOps e trace.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from google.adk.agents import LlmAgent
from google.genai import types

from cabe_no_bolso import agent as agent_mod, callbacks, prompt_gi

NOME = agent_mod.NOME
CHAVE_CONTEXTO = "gi_contexto_json"
CONTEXTO_VAZIO = {"modo": "conversa", "gatilho": "pergunta_cliente", "consentimento": False,
                  "cliente": {"primeiro_nome": "Cliente", "publico_vulneravel": False},
                  "fatura": None, "ofertas_liberadas": [], "historico_conversa": []}


def instrucao(ctx) -> str:
    """InstructionProvider: prompt da Gi com o contexto desta sessão/turno. Bypassa a injeção de {chaves} do ADK."""
    texto = ctx.state.get(CHAVE_CONTEXTO)
    return prompt_gi.prompt_agente(texto if isinstance(texto, str) and texto.strip() else CONTEXTO_VAZIO)


def config_geracao(modelo: str | None = None) -> types.GenerateContentConfig:
    bloqueio = types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE
    modelo = modelo or agent_mod.modelo_configurado()
    return types.GenerateContentConfig(
        temperature=float(os.environ.get("TEMPERATURA", "0.2")),
        top_p=0.9,
        max_output_tokens=int(os.environ.get("MAX_TOKENS_SAIDA", "2048")),
        response_mime_type="application/json",
        thinking_config=agent_mod.config_pensamento(modelo),
        safety_settings=[
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=bloqueio),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=bloqueio),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=bloqueio),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=bloqueio),
        ],
    )


def after_model(callback_context, llm_response):
    """FinOps + trace (sem guardião de texto: a saída é JSON conferida por checagens.checar e pelo validador)."""
    st = callback_context.state
    inicio = callbacks._INICIO_MODELO.pop(callback_context.invocation_id, None)
    latencia = int(round((time.perf_counter() - inicio) * 1000)) if inicio else None
    if getattr(llm_response, "partial", False):
        return None
    callbacks._finops(st, llm_response, latencia)
    um = getattr(llm_response, "usage_metadata", None)
    n_texto = sum(len(p.text or "") for p in ((llm_response.content.parts if llm_response.content else None) or []) if p.text)
    callbacks._log("modelo", sessao=callbacks._sessao_id(callback_context), modo="gi", papel=callbacks.PAPEL_LLM.get(), latencia_ms=latencia, chamada_de_ferramenta=False,
                   tokens_entrada=int(getattr(um, "prompt_token_count", 0) or 0), tokens_saida=int(getattr(um, "candidates_token_count", 0) or 0))
    trace = list(st.get("trace") or [])
    trace.append({"ordem": len(trace) + 1, "etapa": "modelo", "ferramenta": "modelo", "argumentos": {"modelo": getattr(llm_response, "model_version", None), "modo": "gi"},
                  "resumo": f"resposta em JSON ({n_texto} caracteres)", "numeros": [], "duracao_ms": latencia,
                  "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "llm": True})
    st["trace"] = trace
    return None


def criar_agente_gi(modelo: str | None = None) -> LlmAgent:
    modelo = agent_mod.nome_do_modelo(modelo) if modelo else agent_mod.modelo_configurado()
    return LlmAgent(
        name=NOME,
        model=agent_mod.modelo_gemini(modelo),      # Gemini(location=global, retentativas curtas): agent.modelo_gemini
        description="Cabe no Bolso no modo da Gi: conversa e insight a partir de um contexto já calculado; responde em JSON.",
        instruction=instrucao,
        tools=[],
        generate_content_config=config_geracao(modelo),
        before_model_callback=callbacks.before_model,
        after_model_callback=after_model,
    )


__all__ = ["criar_agente_gi", "instrucao", "config_geracao", "after_model", "CHAVE_CONTEXTO", "NOME"]
