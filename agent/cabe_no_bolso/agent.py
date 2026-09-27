"""root_agent do Cabe no Bolso: um único LlmAgent (Gemini) com ferramentas de cabe_core e callbacks de policy.

Modelo sempre por variável de ambiente (MODELO; reserva em MODELO_RESERVA). Vertex AI via ADC:
GOOGLE_GENAI_USE_VERTEXAI=TRUE, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION=global (gemini-3.8-flash só responde em global).
A instrução é um InstructionProvider: instruction.md + o contexto da sessão (cliente, mês, consentimento, o que já foi
calculado), montado em código antes de cada chamada. Assim o modelo conversa a partir das contas, nunca as faz.

O modelo entra como objeto `Gemini` (não como string), com a localização fixa em `global` e retentativas curtas em
408/429/5xx; o agente é embrulhado num `App` com plugins (retentativa de ferramenta e, opcionalmente, o analytics do
ADK no BigQuery). Padrão trazido de guiwatanabe/iai-cabe-no-bolso (Guilherme, Time 05).
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.plugins import ReflectAndRetryToolPlugin
from google.genai import types

from cabe_core import calendario, finops as finops_mod
from cabe_core.dinheiro import brl
from cabe_no_bolso import callbacks, policy, tools

PASTA = Path(__file__).resolve().parent
load_dotenv(PASTA.parent / ".env", override=False)
load_dotenv(PASTA / ".env", override=False)

log = logging.getLogger("cabe_no_bolso.agent")

MODELO_PADRAO = finops_mod.MODELO_PADRAO
NOME = "cabe_no_bolso"
LOCALIZACAO_MODELO = "global"      # gemini-3.8-flash só responde em global (us-central1 dá 404); vale mesmo no Agent Engine
RETENTATIVAS_MODELO = 3            # 408/429/5xx; teto curto para a demo nunca ficar pendurada
RETENTATIVA_ATRASO_MAX_S = 8


def modelo_configurado() -> str:
    return finops_mod.modelo_padrao()


def usa_vertex() -> bool:
    return (os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") or "").strip().lower() in ("1", "true", "yes", "sim", "on")


def nome_do_modelo(modelo) -> str:
    """Nome (string) de um modelo dado como string ou como objeto Gemini/BaseLlm."""
    if isinstance(modelo, str):
        return modelo
    return str(getattr(modelo, "model", modelo))


def modelo_gemini(modelo: str | None = None) -> Gemini:
    """Objeto de modelo do ADK com localização `global` (só no Vertex; a Gemini API não aceita project/location) e
    retentativas com teto curto. Padrão trazido de guiwatanabe/iai-cabe-no-bolso (Guilherme)."""
    nome = nome_do_modelo(modelo) if modelo else modelo_configurado()
    kwargs = {}
    if usa_vertex():
        kwargs["client_kwargs"] = {"location": os.environ.get("MODELO_LOCALIZACAO") or LOCALIZACAO_MODELO}
    return Gemini(model=nome, retry_options=types.HttpRetryOptions(attempts=RETENTATIVAS_MODELO, max_delay=RETENTATIVA_ATRASO_MAX_S), **kwargs)


def plugins() -> list:
    """Plugins do App (padrão trazido de guiwatanabe/iai-cabe-no-bolso, Guilherme):

    - ReflectAndRetryToolPlugin(max_retries=2): quando uma ferramenta levanta exceção, devolve ao modelo o erro com uma
      orientação de reflexão e deixa tentar de novo até 2 vezes; depois disso a exceção sobe e o runtime cai no caminho
      sem LLM (plano B). Respostas com {"erro": ...} não contam: são resultado, não falha.
    - BigQueryAgentAnalyticsPlugin(project_id, dataset_id) só quando BQ_ANALYTICS_DATASET está definida: grava os eventos
      do agente num dataset do BigQuery (custo por cliente, cenário e dia via SQL). Precisa do extra `bigquery`
      (`uv sync --extra bigquery`) e do dataset criado antes (deploy/CHECKLIST.md); sem isso, registra um aviso e segue.
    """
    out = [ReflectAndRetryToolPlugin(max_retries=2)]
    dataset = (os.environ.get("BQ_ANALYTICS_DATASET") or "").strip()
    if dataset:
        projeto = os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
        try:
            from google.adk.plugins.bigquery_agent_analytics_plugin import BigQueryAgentAnalyticsPlugin
            out.append(BigQueryAgentAnalyticsPlugin(project_id=projeto, dataset_id=dataset))
        except Exception as e:  # ImportError (falta google-api-core / extra bigquery) ou credencial
            log.warning("BQ_ANALYTICS_DATASET=%s definido, mas o plugin de analytics não subiu (%s: %s); seguindo sem ele",
                        dataset, type(e).__name__, str(e)[:120])
    return out


def criar_app(agente: LlmAgent, nome: str = NOME) -> App:
    """App do ADK: nome + agente raiz + plugins. O Runner recebe o App (padrão trazido de guiwatanabe/iai-cabe-no-bolso)."""
    return App(name=nome, root_agent=agente, plugins=plugins())


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
    modelo = nome_do_modelo(modelo) if modelo else modelo_configurado()
    return LlmAgent(
        name=NOME,
        model=modelo_gemini(modelo),
        description="Agente do banco que ajuda o cliente a pagar a fatura do cartão com um plano que cabe no mês.",
        instruction=instrucao,
        tools=list(tools.FERRAMENTAS),
        generate_content_config=config_geracao(modelo),
        before_model_callback=callbacks.before_model,   # cronômetro + bloqueio de injeção (entrada é dado)
        after_model_callback=callbacks.after_model,     # guardião + FinOps + trace
        before_tool_callback=callbacks.before_tool,     # teto de chamadas de ferramenta por turno + consentimento
        after_tool_callback=callbacks.after_tool,
    )


root_agent = criar_agente()
app = criar_app(root_agent)     # adk web / adk run carregam `app` (com os plugins) antes de `root_agent`
