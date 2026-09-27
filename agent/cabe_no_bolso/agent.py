"""root_agent do Cabe no Bolso: um único LlmAgent (Gemini) com ferramentas de cabe_core e callbacks de policy.

Modelo sempre por variável de ambiente (MODELO; reserva em MODELO_RESERVA). Vertex AI via ADC:
GOOGLE_GENAI_USE_VERTEXAI=TRUE, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION=global (gemini-3.8-flash só responde em global).
A instrução é um InstructionProvider: instruction.md + o contexto da sessão (cliente, mês, consentimento, o que já foi
calculado), montado em código antes de cada chamada. Assim o modelo conversa a partir das contas, nunca as faz.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.genai import types

from cabe_core import calendario
from cabe_core.dinheiro import brl
from cabe_no_bolso import callbacks, policy, tools

PASTA = Path(__file__).resolve().parent
load_dotenv(PASTA.parent / ".env", override=False)
load_dotenv(PASTA / ".env", override=False)

MODELO_PADRAO = "gemini-3.8-flash"
NOME = "cabe_no_bolso"


def modelo_configurado() -> str:
    return os.environ.get("MODELO") or MODELO_PADRAO


@lru_cache(maxsize=1)
def instrucao_base() -> str:
    return (PASTA / "instruction.md").read_text(encoding="utf-8")


def contexto_da_sessao(state) -> str:
    """Bloco curto com o que o código já sabe da sessão. Sem histórico de pagamentos, sem extrato bruto."""
    cid = state.get("cliente_id")
    if not cid:
        return ("## Contexto desta sessão\n\nSessão sem cliente definido (modo de desenvolvimento). Peça ao usuário o identificador "
                "do cliente e o mês (AAAAMM) e passe-os para as ferramentas.")
    persona = policy.persona_demo(cid) or {}
    apelido = state.get("apelido") or persona.get("apelido") or "cliente"
    anomes = int(state.get("anomes") or 0)
    linhas = [
        "## Contexto desta sessão",
        "",
        f"- Cliente: {apelido} (identificador já na sessão; não o repita ao cliente).",
        f"- Mês da fatura: {calendario.rotulo(anomes) if anomes else 'desconhecido'}. Data de hoje (simulada): {state.get('data_simulada') or 'não informada'}.",
        f"- Consentimento registrado: {'sim' if state.get('consentimento') else 'não'}.",
    ]
    esc = state.get("escolha_pagamento")
    if esc:
        linhas.append(f"- O cliente tocou em '{esc.get('rotulo')}' na tela de pagamento ({brl(int(esc.get('valor', 0)))}): entre antes da confirmação, com a opção que cabe (cenário 1, 2 ou 3).")
    m = state.get("motor")
    o = state.get("ofertas")
    if m:
        linhas.append("- Análise do mês já feita pelo motor (use estes dados; só chame analisar_fatura de novo para contar o PIX):")
        linhas.append("```json\n" + json.dumps(tools._resumo_motor(m, state), ensure_ascii=False) + "\n```")
    if o:
        linhas.append("- Saídas já montadas pela policy (use estes dados; não chame listar_ofertas de novo sem a análise ter mudado):")
        linhas.append("```json\n" + json.dumps(tools._resumo_ofertas(o), ensure_ascii=False) + "\n```")
        if o.get("encaminhar_humano") or o.get("recomendada") is None:
            linhas.append("- Sem crédito neste caso: só formas de pagar a fatura e uma pessoa; não mencione crédito.")
    if state.get("plano"):
        p = state["plano"]
        linhas.append(f"- Plano confirmado: {p.get('rotulo_cliente')}, parcela {brl(int(p.get('parcela', 0)))}, termina em {calendario.rotulo(int(p.get('termina_em', anomes or 202501)))}. Não ofereça outra coisa.")
    if state.get("recusou_oferta"):
        linhas.append("- O cliente recusou a oferta neste ciclo: não volte ao assunto; só responda o que ele perguntar.")
    if state.get("encaminhado"):
        linhas.append("- Já encaminhado para uma pessoa: encerre com gentileza, sem oferta.")
    usadas = state.get("ferramentas_usadas") or []
    if usadas:
        linhas.append(f"- Ferramentas já usadas: {', '.join(usadas)}.")
    return "\n".join(linhas)


def instrucao(ctx) -> str:
    """InstructionProvider: instrução fixa + contexto da sessão. Bypassa a injeção de {chaves} do ADK."""
    return instrucao_base() + "\n\n" + contexto_da_sessao(ctx.state)


def config_pensamento(modelo: str) -> types.ThinkingConfig | None:
    """Raciocínio curto: o modelo só conversa a partir de contas prontas; pensar longo custa tokens e latência.

    PENSAMENTO=minimal|low|medium|high (Gemini 3.x, thinking_level) ou budget:<n> (Gemini 2.5, thinking_budget).
    Padrão 'budget:0' (thinking_budget=0, o mínimo): o gemini-3.8-flash no Vertex (global) aceita thinking_budget=0 e
    devolve 400 "Thinking level is unsupported: THINKING_LEVEL_MINIMAL" para thinking_level=minimal (27/09; evals com
    budget:0 em agent/evals/resultado-gi-2026-09-27.md: 22 de 22 exemplos da Gi). 'low' continua disponível por PENSAMENTO.
    """
    esc = (os.environ.get("PENSAMENTO") or "budget:0").lower()
    if esc.startswith("budget:"):
        return types.ThinkingConfig(thinking_budget=int(esc.split(":", 1)[1]))
    if esc in ("minimal", "low", "medium", "high"):
        return types.ThinkingConfig(thinking_level=getattr(types.ThinkingLevel, esc.upper()))
    return None


def config_geracao(modelo: str | None = None) -> types.GenerateContentConfig:
    bloqueio = types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE
    modelo = modelo or modelo_configurado()
    return types.GenerateContentConfig(
        temperature=float(os.environ.get("TEMPERATURA", "0.2")),
        top_p=0.9,
        max_output_tokens=int(os.environ.get("MAX_TOKENS_SAIDA", "2048")),   # inclui os tokens de raciocínio
        thinking_config=config_pensamento(modelo),
        safety_settings=[
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=bloqueio),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=bloqueio),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=bloqueio),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=bloqueio),
        ],
    )


def criar_agente(modelo: str | None = None) -> LlmAgent:
    modelo = modelo or modelo_configurado()
    return LlmAgent(
        name=NOME,
        model=modelo,
        description="Agente do banco que ajuda o cliente a pagar a fatura do cartão com um plano que cabe no mês.",
        instruction=instrucao,
        tools=list(tools.FERRAMENTAS),
        generate_content_config=config_geracao(modelo),
        before_model_callback=callbacks.before_model,
        after_model_callback=callbacks.after_model,
        before_tool_callback=callbacks.before_tool,
        after_tool_callback=callbacks.after_tool,
    )


root_agent = criar_agente()
