"""Runtime do agente: Runner + InMemorySessionService, com a conversa no formato de POST /api/mensagem.

Interface estável para o servidor (server/main.py):
  criar_runner(modelo=None, modo=None) -> Runner
  conversar(sessao_id, cliente_id, anomes, texto_ou_acao, valor=None, modelo=None) -> dict   # /api/mensagem
  criar_sessao(cliente_id, anomes, persona=None, sessao_id=None, simulacao=None) -> dict      # /api/sessao (sem LLM)
  consentir(sessao_id, concedido) -> dict                                                     # /api/consentimento (sem LLM)
  avancar_mes(sessao_id) -> dict                                                              # /api/avancar-mes (sem LLM)
  trace(sessao_id) -> list; painel(sessao_id) -> dict; saude() -> dict
Todas têm versão *_async; as síncronas usam asyncio.run (chamar de thread sem loop, como endpoints sync do FastAPI).

Dois modos de conversa (MODO_CONVERSA, padrão MODO_PADRAO):
- gi   : o servidor monta o contexto JSON já calculado (contexto.montar), o LLM recebe o prompt da Gi verbatim e responde
         em JSON; fluxo contexto -> agente -> checagens em código -> validador -> (regenera 1x) -> mensagem segura.
         O LLM não tem ferramentas de dados; abrir_resumo_contrato/transferir_humano/revogar_consentimento viram
         chamadas determinísticas a tools.* pelo runtime.
- tools: o agente com ferramentas (agent.py + instruction.md) e o guardião de texto; depois do guardião entra o mesmo
         validador, com a mesma política de regeneração e mensagem segura.
Onde o LLM entra: só em conversar() com ações conversacionais ou texto livre (e no insight, se INSIGHT_COM_LLM=true).
Pagar mínimo/outro valor, consentimento, acompanhamento e painel são determinísticos (cabe_core direto), como pede
docs/05 (princípio 14). Sessão em memória: deploy com 1 instância (--min-instances=1 --max-instances=1 --session-affinity).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from cabe_core import acompanhar, calendario, capacidade, fatura as fatura_mod, finops as finops_core, painel as painel_mod
from cabe_core.dinheiro import brl, coletar_numeros
from cabe_no_bolso import agent as agent_mod, agente_gi, callbacks, checagens, contexto as contexto_mod, policy, prompt_gi, tools, validador

APP = agent_mod.NOME
MODO_PADRAO = "gi"          # decidido pelos evals de 27/09 (agent/evals/resultado-gi-2026-09-27.md); 'tools' continua disponível
MENSAGEM_ERRO_DADOS = "Não consegui ler seu extrato agora. Posso tentar de novo ou te passar para uma pessoa."
MENSAGEM_ERRO_MODELO = "Não consegui responder agora. Posso tentar de novo ou te passar para uma pessoa."
# Modo tools, turno em que a ferramenta confirmar_plano rodou: texto fixo (sem número), o mesmo do 'confirmar' em código do modo gi.
TEXTO_PLANO_CONFIRMADO = ("Combinado. Te mostro o resumo do contrato antes de qualquer coisa e só seguimos com a sua confirmação. "
                          "Te acompanho nas próximas três faturas.")
ACOES = {
    "ver_opcoes": "Ver opções.",
    "consigo_pagar": "Consigo pagar minha fatura?",
    "por_que_alta": "Por que minha fatura veio tão alta?",
    "confirmar": "Sim, quero essa opção. Pode confirmar.",
    "falar_com_pessoa": "Quero falar com uma pessoa.",
    "nao_quero": "Não quero isso. Prefiro continuar com o que escolhi.",
    "pagar_minimo": "Vou pagar só o mínimo.",
    "pagar_outro_valor": "Vou pagar outro valor.",
    "contar_pix": "Sim, o PIX que entra todo mês é renda minha. Pode contar com ele nas contas.",
    "confirmar_entrada_regular": "Sim, o PIX que entra todo mês é renda minha. Pode contar com ele nas contas.",
    "nao_contar_pix": "Não, o PIX mensal não é renda certa. Deixe de fora das contas.",
}
# frases que o servidor (server/conversa.py, ACOES_RUNTIME_TEXTO) manda no lugar da ação: viram a ação de novo aqui
TEXTOS_ACAO = {
    "Sim, o PIX mensal é renda minha. Pode contar com ele nas contas.": "contar_pix",
    "Não, o PIX mensal não é renda. Deixe de fora das contas.": "nao_contar_pix",
    ACOES["contar_pix"]: "contar_pix",
    ACOES["nao_contar_pix"]: "nao_contar_pix",
}
ACOES_SEM_LLM = ("pagar_minimo", "pagar_outro_valor")
ACOES_ENTRADA_REGULAR = ("contar_pix", "confirmar_entrada_regular", "nao_contar_pix")
CHIP_PESSOA = {"rotulo": "Falar com uma pessoa", "acao": "falar_com_pessoa", "secundario": True}
CHIPS_PIX = [{"rotulo": "Sim, é renda", "acao": "confirmar_entrada_regular"}, {"rotulo": "Não é renda", "acao": "nao_contar_pix", "secundario": True}]
FERRAMENTA_DA_ACAO = {"abrir_resumo_contrato": "confirmar_plano", "transferir_humano": "encaminhar_humano", "mostrar_oferta": "listar_ofertas",
                      "revogar_consentimento": "registrar_consentimento"}
ACAO_DA_FERRAMENTA = {"confirmar_plano": "abrir_resumo_contrato", "encaminhar_humano": "transferir_humano", "listar_ofertas": "mostrar_oferta",
                      "registrar_consentimento": "revogar_consentimento"}
HISTORICO_MAX = 12

_SESSOES = InMemorySessionService()      # compartilhado pelos runners dos dois modos: o estado da sessão é um só
_RUNNERS: dict[tuple[str, str], Runner] = {}
_REGISTRO: dict[str, dict] = {}     # sessao_id -> {cliente_id, modelo}


# ------------------------------------------------------------------ configuração por ambiente
def modo_conversa() -> str:
    """'gi' | 'tools'. MODO_CONVERSA=auto|sem_llm (valores do servidor) caem no padrão."""
    m = (os.environ.get("MODO_CONVERSA") or "").strip().lower()
    return m if m in ("gi", "tools") else MODO_PADRAO


def acompanhamento_com_llm() -> bool:
    return (os.environ.get("ACOMPANHAMENTO_COM_LLM") or "false").strip().lower() in ("1", "true", "sim", "yes", "on")


def insight_com_llm() -> bool:
    return (os.environ.get("INSIGHT_COM_LLM") or "false").strip().lower() in ("1", "true", "sim", "yes", "on")


def configuracao() -> dict:
    return {"modo_conversa": modo_conversa(), "validador": {"ativo": validador.ativo(), "modelo": validador.modelo_configurado()},
            "acompanhamento_com_llm": acompanhamento_com_llm(), "insight_com_llm": insight_com_llm(), "modelo": agent_mod.modelo_configurado()}


# ------------------------------------------------------------------ infraestrutura
def criar_runner(modelo: str | None = None, modo: str | None = None) -> Runner:
    """Um Runner por (modo, modelo), todos sobre o mesmo InMemorySessionService. Reutilizado entre chamadas.

    O Runner recebe um App (agent.criar_app: nome + agente + plugins de retentativa de ferramenta e, se BQ_ANALYTICS_DATASET,
    analytics no BigQuery), padrão trazido de guiwatanabe/iai-cabe-no-bolso. O modelo é sempre um nome (string) aqui;
    o objeto Gemini nasce em agent.modelo_gemini.
    """
    nome = agent_mod.nome_do_modelo(modelo) if modelo else agent_mod.modelo_configurado()
    modo = modo or modo_conversa()
    chave = (modo, nome)
    if chave not in _RUNNERS:
        if modo == "gi":
            agente = agente_gi.criar_agente_gi(nome)
        else:
            agente = agent_mod.root_agent if nome == agent_mod.nome_do_modelo(agent_mod.root_agent.model) else agent_mod.criar_agente(nome)
        _RUNNERS[chave] = Runner(app=agent_mod.criar_app(agente, nome=APP), session_service=_SESSOES)
    return _RUNNERS[chave]


def nome_do_modelo(runner: Runner) -> str:
    return agent_mod.nome_do_modelo(runner.agent.model)


def _executar(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("há um event loop ativo: use a versão *_async desta função")


def _runner_da_sessao(sessao_id: str, modelo: str | None = None, modo: str | None = None) -> Runner:
    reg = _REGISTRO.get(sessao_id) or {}
    return criar_runner(modelo or reg.get("modelo"), modo)


async def _obter_sessao(runner: Runner, sessao_id: str, cliente_id: str | None = None):
    reg = _REGISTRO.get(sessao_id) or {}
    uid = cliente_id or reg.get("cliente_id")
    if not uid:
        return None
    return await runner.session_service.get_session(app_name=APP, user_id=uid, session_id=sessao_id)


async def _aplicar_delta(runner: Runner, sessao, delta: dict) -> None:
    """Grava mudanças de estado fora de uma invocação do agente (caminho determinístico), pelo evento do ADK."""
    # author "user" sem conteúdo: o roteador do ADK ignora eventos de usuário e o montador de contexto pula eventos vazios
    ev = Event(author="user", invocation_id=f"sys-{uuid.uuid4().hex[:12]}", actions=EventActions(state_delta=delta))
    await runner.session_service.append_event(sessao, ev)


class _EstadoLocal(dict):
    """Estado dict com rastreio das chaves alteradas, para rodar as ferramentas fora do agente e gravar só o delta."""

    def __init__(self, base: dict):
        super().__init__(base)
        self.alteradas: set[str] = set()

    def __setitem__(self, k, v):
        self.alteradas.add(k)
        super().__setitem__(k, v)


def _ctx(sessao) -> SimpleNamespace:
    return SimpleNamespace(state=_EstadoLocal(dict(sessao.state)), session=sessao, function_call_id=None)


def _delta(ctx: SimpleNamespace) -> dict:
    return {k: ctx.state[k] for k in ctx.state.alteradas}


def _trace_add(state, ferramenta: str, argumentos: dict, resumo: str, numeros: list[dict], duracao_ms: int | None, etapa: str,
               llm: bool = False, **extra) -> None:
    trace = list(state.get("trace") or [])
    trace.append({"ordem": len(trace) + 1, "etapa": etapa, "ferramenta": ferramenta, "argumentos": argumentos, "resumo": resumo,
                  "numeros": numeros, "duracao_ms": duracao_ms, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  "llm": llm, **extra})
    state["trace"] = trace


def _data_simulada(anomes: int, vencimento_dia: int) -> str:
    ano, mes = divmod(int(anomes), 100)
    dia = max(1, int(vencimento_dia) - 7)
    return f"{dia:02d}/{mes:02d}/{ano}"


def _erro(erro: str, mensagem: str) -> dict:
    return {"erro": erro, "mensagem_cliente": mensagem}


# ------------------------------------------------------------------ /api/sessao
async def criar_sessao_async(cliente_id: str, anomes: int, persona: str | None = None, sessao_id: str | None = None,
                             simulacao: dict | None = None, modelo: str | None = None) -> dict:
    runner = criar_runner(modelo)
    fonte = tools.fonte()
    anomes = int(anomes)
    # Antes do consentimento só a fatura do mês (o que o app já mostra): n=1. O histórico de 12 meses e o grupo
    # calculado só entram depois do "sim" (capacidade.motor). O rótulo do grupo aqui vem do config (personas_demo),
    # só para o seletor/painel da banca; nunca vai ao cliente.
    hist = fatura_mod.historico(fonte, cliente_id, anomes, n=1)
    atual = next((f for f in hist if f["anomes"] == anomes), None)
    if atual is None:
        return _erro("dados_indisponiveis", MENSAGEM_ERRO_DADOS)
    taxas = policy.registro_taxas()
    p = policy.persona_demo(cliente_id) or {}
    rotulos = taxas.get("grupos", {}).get("rotulos", {}) or {}
    g = {"rotulo": rotulos.get(p.get("grupo_esperado"), p.get("grupo_esperado")) if p else None}
    apelido = (persona or "").strip().capitalize() if persona and not p else p.get("apelido") or "Cliente"
    minimo = fatura_mod.minimo(atual["fatura"], float(taxas["rotativo"].get("minimo_pct_fatura", 0.15)))
    fat = {"valor": int(atual["fatura"]), "vencimento_dia": int(atual["dia_vencimento"]), "minimo": int(minimo)}
    numeros = coletar_numeros({"fatura": fat}, "fatura.historico") + [{"valor": fat["valor"] - int(atual["pago"]), "origem": "fatura.historico:nao_pago"},
                                                                       {"valor": int(atual["pago"]), "origem": "fatura.historico:pago"}]
    data_sim = _data_simulada(anomes, fat["vencimento_dia"])
    numeros.append({"valor": int(data_sim[:2]), "origem": "sessao:data_simulada.dia"})
    sid = sessao_id or uuid.uuid4().hex[:12]
    estado = {
        "cliente_id": cliente_id, "anomes": anomes, "apelido": apelido, "perfil_persona": p.get("perfil"),
        "grupo_rotulo": g["rotulo"], "data_simulada": data_sim, "consentimento": False, "consentimento_registro": None,
        "mes_simulado": anomes, "plano": None, "motor": None, "ofertas": None, "ciclos": [], "escolha_pagamento": None,
        "numeros_validados": numeros, "trace": [], "guardiao": {"removidos": [], "termos_bloqueados": [], "substituicoes": 0},
        "finops": {"chamadas_llm": 0, "chamadas_validador": 0, "tokens_entrada": 0, "tokens_saida": 0, "tokens_entrada_validador": 0, "tokens_saida_validador": 0, "latencias_ms": [], "custo_estimado": None},
        "ferramentas_usadas": [], "historico_contratacoes": list((simulacao or {}).get("historico_contratacoes") or []),
        "simulacao": {k: v for k, v in (simulacao or {}).items() if k in ("taxas", "liberacao", "status_cobertura", "publico_vulneravel")} or None,
        "recusou_oferta": False, "encaminhado": None, "cards_enviados": [], "historico_conversa": [], "validador_reprovacoes": [],
        "modo_conversa": modo_conversa(), "turnos_llm": 0, "proativa_enviada_em": None, "permissao_ampliacao": False,
    }
    _trace_add(estado, "fatura.historico", {"cliente_id": cliente_id[:8] + "…", "anomes": anomes, "n": 1},
               f"fatura do mês reconstruída pelo modo {atual['modo']} (só o mês; histórico só depois do consentimento)",
               numeros[:3], 0, "sessao")
    sess = await runner.session_service.create_session(app_name=APP, user_id=cliente_id, session_id=sid, state=estado)
    _REGISTRO[sess.id] = {"cliente_id": cliente_id, "modelo": nome_do_modelo(runner)}
    return {
        "sessao_id": sess.id, "modo": "api", "modo_conversa": estado["modo_conversa"],
        "cliente": {"apelido": apelido, "perfil": p.get("perfil"), "grupo_rotulo": g["rotulo"]},
        "anomes": anomes, "mes_rotulo": calendario.rotulo(anomes), "data_simulada": f"{data_sim} (simulada)",
        "fatura": {**fat, "opcoes_pagamento": [
            {"rotulo": "Pagar o total", "valor": fat["valor"], "acao": "pagar_total"},
            {"rotulo": "Pagar o mínimo", "valor": fat["minimo"], "acao": "pagar_minimo"},
            {"rotulo": "Outro valor", "valor": int(atual["pago"]), "acao": "pagar_outro_valor", "valor_gravado": int(atual["pago"])},
        ]},
        "consentimento": False, "numeros_validados": numeros, "simulado": True,
    }


def criar_sessao(cliente_id: str, anomes: int, persona: str | None = None, sessao_id: str | None = None,
                 simulacao: dict | None = None, modelo: str | None = None) -> dict:
    return _executar(criar_sessao_async(cliente_id, anomes, persona, sessao_id, simulacao, modelo))


# ------------------------------------------------------------------ /api/consentimento (sem LLM; insight pelo LLM só com INSIGHT_COM_LLM)
def _insight(state) -> dict:
    m = state.get("motor")
    o = state.get("ofertas")
    if not state.get("consentimento") or not m:
        fat = _fatura_de(state)
        return {"estado": "sem_adesao", "texto": f"Sua fatura fechou em {brl(fat['valor'])} e vence dia {fat['vencimento_dia']}. Veja as formas de pagar.",
                "botao": {"rotulo": "Ver formas de pagar", "acao": "ver_formas_de_pagar"}}
    fat = m["fatura"]
    if m["cabe"]:
        return {"estado": "cabe", "texto": f"Sua fatura de {brl(fat['valor'])} cabe no seu mês: dá para pagar o total pela conta no dia {fat['vencimento_dia']}.",
                "botao": None}
    if o and (o.get("encaminhar_humano") or o.get("recomendada") is None):
        return {"estado": "sem_credito", "texto": f"Sua fatura fechou em {brl(fat['valor'])} e vence dia {fat['vencimento_dia']}. Veja as formas de pagar.",
                "botao": {"rotulo": "Ver formas de pagar", "acao": "ver_opcoes"}}
    if m["tipo_falta"] == "pontual":
        return {"estado": "falta_pontual",
                "texto": (f"Sua fatura fechou em {brl(fat['valor'])}. Até o dia {fat['vencimento_dia']}, a previsão é ter {brl(max(0, m['folga']))} "
                          f"pela conta; o dinheiro volta em {m['dias_ate_recebimento']} dias. Temos opções para pagar tudo."),
                "botao": {"rotulo": "Ver opções no ia.i", "acao": "ver_opcoes"}}
    return {"estado": "falta_que_se_repete",
            "texto": (f"Sua fatura fechou em {brl(fat['valor'])} e vence dia {fat['vencimento_dia']}. Pelo seu mês, cabe pagar {brl(max(0, m['folga']))}; "
                      f"faltam {brl(m['falta'])}. Dá para juntar o que falta numa parcela que cabe."),   # <= 160 caracteres (checagem tamanho)
            "botao": {"rotulo": "Conversar com o ia.i", "acao": "ver_opcoes"}}


def _fatura_de(state) -> dict:
    m = state.get("motor")
    if m:
        return m["fatura"]
    nums = state.get("numeros_validados") or []
    pega = lambda o: next((n["valor"] for n in nums if n["origem"] == o), 0)  # noqa: E731
    return {"valor": pega("fatura.historico:fatura.valor"), "vencimento_dia": pega("fatura.historico:fatura.vencimento_dia"),
            "minimo": pega("fatura.historico:fatura.minimo")}


async def _analise_deterministica(runner: Runner, sessao, contar_pix: bool | None = None) -> dict:
    """Roda motor + ofertas pelo caminho determinístico (gatilho do fechamento ou PIX confirmado), gravando estado e trace."""
    ctx = _ctx(sessao)
    usadas_antes = list(ctx.state.get("ferramentas_usadas") or [])
    t0 = time.perf_counter()
    n0 = len(ctx.state.get("numeros_validados") or [])
    try:
        tools.analisar_fatura(contar_pix=bool(contar_pix), tool_context=ctx)
    except capacidade.DadosIndisponiveis as e:
        return _erro("dados_indisponiveis", f"{MENSAGEM_ERRO_DADOS} ({e})")
    m = ctx.state["motor"]
    _trace_add(ctx.state, "capacidade.motor", {"anomes": ctx.state["anomes"], "contar_pix": bool(ctx.state.get("contar_pix"))},
               m["frase"], list(ctx.state["numeros_validados"])[n0:], int((time.perf_counter() - t0) * 1000), "analise")
    t1 = time.perf_counter()
    n1 = len(ctx.state["numeros_validados"])
    tools.listar_ofertas(tool_context=ctx)
    o = ctx.state["ofertas"]
    _trace_add(ctx.state, "ofertas.montar", {"caminho": o["caminho"], "grupo": o["grupo"]}, o["motivo"],
               list(ctx.state["numeros_validados"])[n1:], int((time.perf_counter() - t1) * 1000), "ofertas")
    ctx.state["ferramentas_usadas"] = usadas_antes      # o modelo ainda não viu essas saídas na conversa
    await _aplicar_delta(runner, sessao, _delta(ctx))
    return {}


async def consentir_async(sessao_id: str, concedido: bool, modelo: str | None = None) -> dict:
    runner = _runner_da_sessao(sessao_id, modelo)
    sessao = await _obter_sessao(runner, sessao_id)
    if sessao is None:
        return _erro("sessao_inexistente", MENSAGEM_ERRO_DADOS)
    ctx = _ctx(sessao)
    r = tools.registrar_consentimento(bool(concedido), tool_context=ctx)
    _trace_add(ctx.state, "registrar_consentimento", {"concedido": bool(concedido)}, r["proximo_passo"], [], 0, "consentimento")
    await _aplicar_delta(runner, sessao, _delta(ctx))
    sessao = await _obter_sessao(runner, sessao_id)
    if concedido:
        err = await _analise_deterministica(runner, sessao)
        if err:
            return err
        sessao = await _obter_sessao(runner, sessao_id)
    st = sessao.state
    insight = _insight(st)
    if concedido and insight_com_llm() and modo_conversa() == "gi":
        r_ins = await insight_gi_async(sessao_id, modelo=modelo)
        if not r_ins.get("erro"):
            insight = r_ins["insight"]
            st = (await _obter_sessao(runner, sessao_id)).state
    return {"consentimento": bool(st.get("consentimento")), "registro": st.get("consentimento_registro"),
            "insight": insight, "numeros_validados": list(st.get("numeros_validados") or []), "simulado": True}


def consentir(sessao_id: str, concedido: bool, modelo: str | None = None) -> dict:
    return _executar(consentir_async(sessao_id, concedido, modelo))


# ------------------------------------------------------------------ pagar mínimo / outro valor (sem LLM)
async def _escolher_pagamento(runner: Runner, sessao, acao: str, valor: int | None) -> dict:
    st = sessao.state
    fat = _fatura_de(st)
    if acao == "pagar_minimo":
        escolha = {"acao": acao, "rotulo": "pagar o mínimo", "valor": int(fat["minimo"])}
    else:
        v = int(valor) if valor is not None else int(next((n["valor"] for n in st.get("numeros_validados") or [] if n["origem"] == "fatura.historico:pago"), 0))
        escolha = {"acao": acao, "rotulo": "pagar outro valor", "valor": max(0, min(v, int(fat["valor"])))}
    delta = {"escolha_pagamento": escolha, "recusou_oferta": False}
    nums = list(st.get("numeros_validados") or [])
    if not any(n["valor"] == escolha["valor"] and n["origem"] == "escolha_pagamento:valor" for n in nums):
        nums.append({"valor": escolha["valor"], "origem": "escolha_pagamento:valor"})
        nums.append({"valor": int(fat["valor"]) - escolha["valor"], "origem": "escolha_pagamento:nao_pago"})
        delta["numeros_validados"] = nums
    await _aplicar_delta(runner, sessao, delta)
    o = st.get("ofertas")
    m = st.get("motor")
    if not st.get("consentimento") or not m:
        card = {"tipo": "aviso", "dados": {"texto": f"Sem a permissão para olhar seu mês, mostro só as formas de pagar: total {brl(fat['valor'])} ou mínimo {brl(fat['minimo'])}.",
                                           "paga_agora": escolha["valor"]}}
        return {"mensagens": [], "cards": [card], "numeros_validados": nums, "guardiao": st.get("guardiao"), "sugestoes": [], "simulado": True}
    if o and o.get("opcoes") and o.get("recomendada") is not None:
        rec = o["opcoes"][o["recomendada"]]
        texto = ("Antes de confirmar: tem um jeito de pagar a fatura inteira com uma parcela que cabe no seu mês."
                 if o["caminho"] == "parcelamento" else
                 "Antes de confirmar: tem um jeito de pagar a fatura inteira com uma cobertura curta que cabe no seu mês.")
        card = {"tipo": "insight", "dados": {"estado": "antes_de_confirmar", "texto": texto, "paga_agora": escolha["valor"],
                                             "parcela": rec["parcela"], "botao_primario": {"rotulo": "Ver opção", "acao": "ver_opcoes"},
                                             "botao_secundario": {"rotulo": "Continuar com este valor", "acao": "nao_quero"}}}
    else:
        card = {"tipo": "insight", "dados": {"estado": "sem_credito", "texto": f"Sua fatura fechou em {brl(fat['valor'])}. Veja as formas de pagar.",
                                             "paga_agora": escolha["valor"], "botao_primario": {"rotulo": "Ver formas de pagar", "acao": "ver_opcoes"},
                                             "botao_secundario": {"rotulo": "Continuar com este valor", "acao": "nao_quero"}}}
    return {"mensagens": [], "cards": [card], "numeros_validados": nums, "guardiao": st.get("guardiao"), "sugestoes": [], "simulado": True}


# ------------------------------------------------------------------ /api/mensagem (LLM)
def _texto_do_usuario(texto_ou_acao: str, valor: int | None) -> tuple[str | None, str]:
    t = (texto_ou_acao or "").strip()
    if t in ACOES:
        frase = ACOES[t]
        if t == "pagar_outro_valor" and valor is not None:
            frase = f"Vou pagar {brl(int(valor))}."
        return t, frase
    if t in TEXTOS_ACAO:
        return TEXTOS_ACAO[t], t
    return None, t


def _sugestoes(state, acao: str | None) -> list[dict]:
    if state.get("encaminhado"):
        return []
    if state.get("plano"):
        return [{"rotulo": "Avançar um mês (demo)", "acao": "avancar_mes"}, CHIP_PESSOA]
    o = state.get("ofertas")
    if not state.get("consentimento"):
        return [{"rotulo": "Permitir a análise", "acao": "consentir"}, CHIP_PESSOA]
    if acao == "nao_quero" or state.get("recusou_oferta"):
        return [CHIP_PESSOA]
    if o and o.get("opcoes") and o.get("recomendada") is not None and acao in ("ver_opcoes", "consigo_pagar", "por_que_alta", None):
        return [{"rotulo": "Quero essa opção", "acao": "confirmar"},
                {"rotulo": "Prefiro continuar como está", "acao": "nao_quero", "secundario": True}, CHIP_PESSOA]
    if o and o.get("encaminhar_humano"):
        return [CHIP_PESSOA, {"rotulo": "Agora não", "acao": "nao_quero", "secundario": True}]
    return [{"rotulo": "Ver opções", "acao": "ver_opcoes"}, CHIP_PESSOA]


def _cards(state, acao: str | None, chamadas: list[str], bloqueadas: bool) -> list[dict]:
    cards = []
    m, o, p = state.get("motor"), state.get("ofertas"), state.get("plano")
    enviados = set(state.get("cards_enviados") or [])
    if bloqueadas and not state.get("consentimento"):
        cards.append({"tipo": "aviso", "dados": {"texto": callbacks.MENSAGEM_SEM_CONSENTIMENTO, "pede_consentimento": True}})
    if m and (acao in ("ver_opcoes", "consigo_pagar") or "analisar_fatura" in chamadas or ("diagnostico" not in enviados and acao not in ("confirmar", "falar_com_pessoa", "nao_quero"))):
        cards.append({"tipo": "diagnostico", "dados": m})
    if m and (acao == "por_que_alta" or "detalhar_fatura" in chamadas):
        cards.append({"tipo": "diagnostico", "dados": {**m, "so_categorias": True}})
    if o and (acao in ("ver_opcoes",) or "listar_ofertas" in chamadas or ("comparador" not in enviados and acao in ("consigo_pagar", None) and o.get("opcoes"))):
        if o.get("opcoes") or o.get("encaminhar_humano"):
            cards.append({"tipo": "comparador", "dados": o})
        if o.get("encaminhar_humano") and not p:
            cards.append({"tipo": "encaminhamento", "dados": {"motivo": o["motivo"], "status": "disponivel",
                                                              "texto": "Você pode falar com uma pessoa do time a qualquer momento; ela recebe o contexto desta conversa."}})
    if p and ("confirmar_plano" in chamadas or acao == "confirmar"):
        cards.append(_card_confirmacao(state))
    if state.get("encaminhado") and ("encaminhar_humano" in chamadas or acao == "falar_com_pessoa"):
        cards.append(_card_encaminhado(state["encaminhado"]["motivo"]))
    if acao == "nao_quero":
        cards.append({"tipo": "aviso", "dados": {"texto": "Registrado. A IA não volta ao assunto até a próxima fatura.", "contratado": False}})
    return cards


def _card_confirmacao(state) -> dict:
    o, p = state.get("ofertas"), state["plano"]
    op = (o or {}).get("opcoes", [{}])[state.get("opcao_confirmada") or 0] if o and o.get("opcoes") else {}
    return {"tipo": "confirmacao", "dados": {
        "resumo": (f"Você paga {brl(p['pagar_agora'])} agora pela conta; {brl(p['valor_financiado'])} vira "
                   + (f"{p['n_parcelas']} parcelas de {brl(p['parcela'])}" if not p.get("dias") else f"cobertura por {p['dias']} dias ({brl(p['parcela'])} no total)")
                   + f"; termina em {calendario.rotulo(p['termina_em'])}; até a próxima fatura cabem {brl(p['teto_cartao_mes'])} no cartão."),
        "plano": p, "opcao": op, "teto_cartao_mes": p["teto_cartao_mes"],
        "aviso": "Simulação: nada é contratado. O contrato real só existe depois da aprovação e da assinatura no app."}}


def _card_encaminhado(motivo: str) -> dict:
    return {"tipo": "encaminhamento", "dados": {"motivo": motivo, "status": "encaminhado",
                                                "texto": "Encaminhado para uma pessoa do time de atendimento, com o contexto desta conversa. Nenhum produto foi oferecido."}}


def _card_formas_de_pagar(ctx_gi: dict) -> dict:
    formas = (ctx_gi.get("fatura") or {}).get("formas_de_pagar") or []
    texto = "Formas de pagar a fatura: " + "; ".join(f"{f['forma']} {f['valor']}" for f in formas) + ". Nenhum pagamento real é feito nesta simulação."
    return {"tipo": "opcoes_da_fatura", "dados": {"texto": texto, "formas": formas, "contratado": False}}


def _mensagens_do_agente(texto: str) -> list[dict]:
    blocos = [b.strip() for b in texto.replace("\r", "").split("\n\n") if b.strip()]
    if len(blocos) > 3:
        blocos = blocos[:2] + [" ".join(blocos[2:])]
    return [{"papel": "agente", "texto": b} for b in blocos]


async def _rodar_modelo(runner: Runner, sessao, texto: str) -> list[str]:
    """Uma invocação do agente na sessão. Devolve os textos finais do modelo (sem chamadas de ferramenta nem pensamentos)."""
    textos: list[str] = []
    async for ev in runner.run_async(user_id=sessao.user_id, session_id=sessao.id,
                                     new_message=types.Content(role="user", parts=[types.Part(text=texto)])):
        if ev.author == runner.agent.name and ev.content and ev.content.parts and not getattr(ev, "partial", False):
            for p in ev.content.parts:
                if p.text and not p.function_call and not p.function_response and not getattr(p, "thought", False):
                    textos.append(p.text)
    return textos


async def _modelo(runner: Runner, sessao, texto: str, papel: str = "agente") -> list[str]:
    """_rodar_modelo com o papel da chamada (agente | regeneracao | insight) marcado para o FinOps por papel
    (callbacks.PAPEL_LLM, lido em after_model). Os testes trocam _rodar_modelo por um modelo falso; este envelope fica."""
    token = callbacks.PAPEL_LLM.set(papel)
    try:
        return await _rodar_modelo(runner, sessao, texto)
    finally:
        callbacks.PAPEL_LLM.reset(token)


async def _entradas_bloqueadas(runner: Runner, sessao) -> int:
    """Contador de mensagens bloqueadas antes do modelo (callbacks.bloquear_entrada_insegura), lido do estado atual."""
    s = await runner.session_service.get_session(app_name=APP, user_id=sessao.user_id, session_id=sessao.id)
    return int(((s.state if s else None) or {}).get("entradas_bloqueadas") or 0)


def _resposta_erro_modelo(sessao_id: str, st0, e: Exception) -> dict:
    callbacks._log("erro_modelo", sessao=sessao_id, erro=type(e).__name__, detalhe=str(e)[:300])
    return {**_erro(f"modelo: {type(e).__name__}: {str(e)[:200]}", MENSAGEM_ERRO_MODELO),
            "mensagens": [{"papel": "agente", "texto": MENSAGEM_ERRO_MODELO}], "cards": [], "sugestoes": [CHIP_PESSOA],
            "numeros_validados": list(st0.get("numeros_validados") or []), "guardiao": st0.get("guardiao")}


def _historico_mais(state, cliente: str | None, agente: str | None) -> list[dict]:
    h = list(state.get("historico_conversa") or [])
    if cliente:
        h.append({"papel": "cliente", "texto": cliente[:300]})
    if agente:
        h.append({"papel": "agente", "texto": agente[:600]})
    return h[-HISTORICO_MAX:]


def _finops_turno(st, fin0: dict, duracao: int, ferramentas: list[str], extra_validador: dict | None = None) -> dict:
    """FinOps de um turno: o que cresceu no state desde fin0, separado por papel (tokens do validador à parte) e com o custo
    em USD pelo preço de config/finops.yaml (cabe_core.finops)."""
    fin = st.get("finops") or {}
    lat = list(fin.get("latencias_ms") or [])[len(fin0.get("latencias_ms") or []):]
    v = extra_validador or {}

    def delta(k: str) -> int:
        return int(fin.get(k, 0) or 0) - int(fin0.get(k, 0) or 0)

    turno = {"chamadas_llm": delta("chamadas_llm"), "chamadas_validador": int(v.get("chamadas", 0)),
             "tokens_entrada": delta("tokens_entrada"), "tokens_saida": delta("tokens_saida"),
             "tokens_entrada_validador": delta("tokens_entrada_validador"), "tokens_saida_validador": delta("tokens_saida_validador"),
             "latencias_ms": lat, "latencias_validador_ms": list(v.get("latencias", [])), "duracao_turno_ms": duracao, "ferramentas": ferramentas,
             "modo_conversa": st.get("modo_conversa") or modo_conversa()}
    custo = finops_core.custo_por_papel(turno, agent_mod.modelo_configurado(), validador.modelo_configurado())
    turno["custo_usd"] = custo["total"]["custo_usd"]
    turno["custo_agente_usd"] = custo["agente"]["custo_usd"]
    turno["custo_validador_usd"] = custo["validador"]["custo_usd"]
    turno["chamadas_por_papel"] = _delta_por_papel(fin, fin0)
    return turno


def _delta_por_papel(fin: dict, fin0: dict) -> dict:
    """O que cresceu em finops.chamadas_por_papel desde fin0: {papel: {chamadas, tokens_entrada, tokens_saida, latencias_ms}}."""
    out = {}
    for papel, reg in (fin.get("chamadas_por_papel") or {}).items():
        antes = (fin0.get("chamadas_por_papel") or {}).get(papel) or {}
        d = {k: int(reg.get(k, 0) or 0) - int(antes.get(k, 0) or 0) for k in ("chamadas", "tokens_entrada", "tokens_saida")}
        if d["chamadas"] > 0:
            d["latencias_ms"] = list(reg.get("latencias_ms") or [])[len(antes.get("latencias_ms") or []):]
            d["modelo"] = reg.get("modelo")
            out[papel] = d
    return out


# ------------------------------------------------------------------ validador (nos dois modos)
def _registrar_validacao(state, r: dict, saida: dict, etapa: str) -> None:
    _trace_add(state, "validador", {"modelo": r.get("modelo"), "modo": state.get("modo_conversa") or modo_conversa()},
               validador.resumo(r), [], r.get("latencia_ms"), etapa, llm=True,
               violacoes=list(r.get("violacoes") or []), aprovado=r.get("aprovado"), orientacao=r.get("orientacao"))
    fin = dict(state.get("finops") or {})
    tin, tout = int(r.get("tokens_entrada") or 0), int(r.get("tokens_saida") or 0)
    fin["chamadas_validador"] = int(fin.get("chamadas_validador", 0)) + 1
    fin["tokens_entrada"] = int(fin.get("tokens_entrada", 0)) + tin
    fin["tokens_saida"] = int(fin.get("tokens_saida", 0)) + tout
    fin["tokens_entrada_validador"] = int(fin.get("tokens_entrada_validador", 0)) + tin     # separado por papel (custo e painel)
    fin["tokens_saida_validador"] = int(fin.get("tokens_saida_validador", 0)) + tout
    fin["latencias_validador_ms"] = list(fin.get("latencias_validador_ms") or []) + [r.get("latencia_ms")]
    callbacks.somar_por_papel(fin, "validador", SimpleNamespace(prompt_token_count=tin, candidates_token_count=tout), r.get("latencia_ms"), r.get("modelo"))
    fin["custo_estimado"] = callbacks.custo_corrente(fin)
    state["finops"] = fin
    callbacks._log("modelo", sessao=None, papel="validador", modelo=r.get("modelo"), latencia_ms=r.get("latencia_ms"), tokens_entrada=tin,
                   tokens_saida=tout, custo_usd=finops_core.custo_usd(tin, tout, r.get("modelo"))["custo_usd"], aprovado=r.get("aprovado"), etapa=etapa)
    if r.get("aprovado") is False:
        reps = list(state.get("validador_reprovacoes") or [])
        reps.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "violacoes": r.get("violacoes"),
                     "orientacao": r.get("orientacao"), "saida": json.dumps(saida, ensure_ascii=False)[:800], "etapa": etapa})
        state["validador_reprovacoes"] = reps
        callbacks._log("validador_reprovou", sessao=None, regras=[v["regra"] for v in r.get("violacoes") or []], etapa=etapa)


# ------------------------------------------------------------------ modo gi: um turno (usado pela conversa, pelo insight e pelos evals)
def _texto_correcao_checagens(ch: dict) -> str:
    linhas = ["A resposta anterior não passou nas checagens em código. Gere de novo, no mesmo formato JSON, corrigindo:"]
    for f in ch.get("falhas") or []:
        linhas.append(f"- {f['regra']}: {f['motivo']}" + (f' [trecho: "{f["trecho"][:100]}"]' if f.get("trecho") else ""))
    linhas.append("Cite só números que estão no contexto, exatamente como estão, e liste todos eles em numeros_citados. "
                  "Sem termos internos, sem produtos fora da fatura.")
    return "\n".join(linhas)


async def _turno_gi(runner: Runner, sessao, ctx_gi: dict, indice: dict, mensagem: str, *, modo: str, consentimento: bool,
                    modelo_validador: str | None = None, validar: bool | None = None, proativa_ja_enviada_hoje: bool = False,
                    texto_cliente: str | None = None) -> dict:
    """contexto -> agente -> checagens -> validador -> (regenera 1x) -> mensagem segura. Devolve o resultado do turno.

    O contexto já deve estar em state[agente_gi.CHAVE_CONTEXTO] (gravado pelo chamador). Uma regeneração no máximo por turno.
    """
    validar = validador.ativo() if validar is None else validar
    st_ini = sessao.state
    saida_bruta = ""
    tentativas: list[dict] = []
    val_calls: list[dict] = []
    regeneracoes = 0
    mensagem_segura = False
    motivo_segura = None
    saida: dict | None = None
    r_val: dict | None = None
    erro = None
    bloqueios0 = await _entradas_bloqueadas(runner, sessao)
    entrada_bloqueada = False

    async def gerar(texto_msg: str, tentativa: int) -> tuple[dict | None, dict, str]:
        nonlocal entrada_bloqueada
        papel = "regeneracao" if tentativa > 1 else ("insight" if modo == "insight" else "agente")
        textos = await _modelo(runner, sessao, texto_msg, papel)
        entrada_bloqueada = entrada_bloqueada or (await _entradas_bloqueadas(runner, sessao)) > bloqueios0
        bruto = "\n".join(t for t in textos if t.strip()).strip()
        s = checagens.analisar_saida(bruto)
        ch = checagens.checar(s, ctx_gi, indice, modo=modo, consentimento=consentimento, tentativa=tentativa,
                              proativa_ja_enviada_hoje=proativa_ja_enviada_hoje)
        tentativas.append({"tentativa": tentativa, "checagens": ch, "saida": s, "bruto": bruto[:1500]})
        return s, ch, bruto

    try:
        saida, ch, saida_bruta = await gerar(mensagem, 1)
        if ch["acao"] == "regenerar":
            regeneracoes += 1
            saida, ch, saida_bruta = await gerar(_texto_correcao_checagens(ch), 2)
        if ch["acao"] == "nao_enviar":
            return {"saida": None, "saida_bruta": saida_bruta, "checagens": ch, "validador": None, "mensagem_segura": False, "nao_enviar": True,
                    "regeneracoes": regeneracoes, "tentativas": tentativas, "validacoes": val_calls, "erro": None}
        if not ch["ok"]:                      # 2ª falha de número, oferta inexistente, ação sem consentimento: texto fixo
            saida = checagens.mensagem_segura(ctx_gi, modo)
            mensagem_segura, motivo_segura = True, "checagens: " + "; ".join(f"{f['regra']}: {f['motivo']}" for f in ch["falhas"][:3])
        # entrada bloqueada antes do modelo (injeção): a recusa é texto fixo em código, não passa pelo validador (0 chamadas)
        if validar and not mensagem_segura and saida is not None and not entrada_bloqueada:
            r_val = await validador.validar_async(saida, ctx_gi, _historico_mais(st_ini, texto_cliente, None), modelo=modelo_validador)
            val_calls.append(r_val)
            if r_val.get("aprovado") is False:
                if r_val.get("r8"):
                    saida = checagens.mensagem_segura(ctx_gi, modo, humano=True)
                    mensagem_segura, motivo_segura = True, "validador: R8"
                elif regeneracoes == 0:
                    regeneracoes += 1
                    saida, ch, saida_bruta = await gerar(validador.texto_correcao(r_val), 2)
                    if not ch["ok"]:
                        saida = checagens.mensagem_segura(ctx_gi, modo)
                        mensagem_segura, motivo_segura = True, "checagens após regeneração"
                    else:
                        r_val2 = await validador.validar_async(saida, ctx_gi, _historico_mais(st_ini, texto_cliente, None), modelo=modelo_validador)
                        val_calls.append(r_val2)
                        if r_val2.get("aprovado") is False:
                            saida = checagens.mensagem_segura(ctx_gi, modo, humano=bool(r_val2.get("r8")))
                            mensagem_segura, motivo_segura = True, "validador: reprovado de novo"
                        r_val = r_val2
                else:
                    saida = checagens.mensagem_segura(ctx_gi, modo)
                    mensagem_segura, motivo_segura = True, "validador: reprovado após regeneração por checagens"
    except Exception as e:
        erro = e
    return {"saida": saida, "saida_bruta": saida_bruta, "checagens": tentativas[-1]["checagens"] if tentativas else None,
            "validador": r_val, "mensagem_segura": mensagem_segura, "motivo_segura": motivo_segura, "nao_enviar": False,
            "regeneracoes": regeneracoes, "tentativas": tentativas, "validacoes": val_calls, "erro": erro,
            "entrada_bloqueada": entrada_bloqueada}


def _gatilho(st, acao: str | None) -> str:
    if st.get("plano") and acompanhamento_com_llm():
        return "acompanhamento"
    if int(st.get("turnos_llm") or 0) == 0 and acao in ("ver_opcoes", "consigo_pagar"):
        return "fechamento"
    return "pergunta_cliente"


async def _montar_contexto_sessao(runner: Runner, sessao, modo: str, gatilho: str, manter_ofertas: bool = False) -> dict:
    """contexto.montar com o que a sessão já tem (motor, ofertas, plano, ciclos, escolha); grava índice e números no estado.

    manter_ofertas=True (validador do modo tools): com plano confirmado, contexto.montar deixa ofertas_liberadas vazio
    (nenhuma oferta nova); para o validador julgar o texto que confirma o plano, as ofertas do mês voltam ao contexto
    com o mesmo índice de origem (o plano é uma delas)."""
    st = sessao.state
    ctx_tool = _ctx(sessao)
    taxas = tools.taxas_da_sessao(ctx_tool)
    lib = tools.liberacao_da_sessao(ctx_tool, st["cliente_id"], taxas) if st.get("consentimento") else None
    sim = st.get("simulacao") or {}
    t0 = time.perf_counter()
    comum = dict(contar_pix=st.get("contar_pix"), historico=None, taxas=taxas, motor=st.get("motor"), ofertas=st.get("ofertas"), liberacao=lib,
                 historico_contratacoes=st.get("historico_contratacoes"), ciclo=(st.get("ciclos") or [None])[-1], apelido=st.get("apelido"),
                 escolha_pagamento=st.get("escolha_pagamento"), status_cobertura=sim.get("status_cobertura"),
                 publico_vulneravel=sim.get("publico_vulneravel"))
    r = contexto_mod.montar(st["cliente_id"], int(st["anomes"]), modo, gatilho, bool(st.get("consentimento")), plano=st.get("plano"), **comum)
    if manter_ofertas and st.get("plano") and st.get("ofertas") and not r["contexto"].get("ofertas_liberadas"):
        r_of = contexto_mod.montar(st["cliente_id"], int(st["anomes"]), modo, gatilho, bool(st.get("consentimento")), plano=None, **comum)
        r["contexto"]["ofertas_liberadas"] = r_of["contexto"].get("ofertas_liberadas") or []
        r["ids"] = r_of["ids"]
        r["indice"] = {**r_of["indice"], **r["indice"]}
        r["numeros"] = list(r["numeros"]) + [n for n in r_of["numeros"] if n not in r["numeros"]]
    ctx_tool.state["gi_indice"] = r["indice"]
    ctx_tool.state["gi_ids"] = r["ids"]
    ctx_tool.state[agente_gi.CHAVE_CONTEXTO] = prompt_gi.contexto_json_texto(r["contexto"])
    n0 = len(ctx_tool.state.get("numeros_validados") or [])
    tools.registrar_numeros(ctx_tool.state, r["numeros"])
    _trace_add(ctx_tool.state, "contexto.montar", {"modo": modo, "gatilho": gatilho, "campos": len(r["contexto"]), "ofertas_liberadas": len(r["contexto"]["ofertas_liberadas"])},
               contexto_mod.resumo_para_trace(r["contexto"]), list(ctx_tool.state["numeros_validados"])[n0:], int((time.perf_counter() - t0) * 1000), "contexto")
    await _aplicar_delta(runner, sessao, _delta(ctx_tool))
    return r


async def _efeitos_da_acao(runner: Runner, sessao, saida: dict, ctx_gi: dict, ids: dict, acao_chip: str | None) -> tuple[list[dict], list[str]]:
    """Executa em código o que a ação pede (ferramentas de cabe_core) e monta os cards. Devolve (cards, ferramentas)."""
    st = sessao.state
    ctx_tool = _ctx(sessao)
    acao = saida.get("acao") or "nenhuma"
    oid = saida.get("oferta_id")
    cards: list[dict] = []
    ferramentas: list[str] = []
    enviados = set(st.get("cards_enviados") or [])
    m, o = st.get("motor"), st.get("ofertas")
    if acao == "mostrar_oferta":
        if m and "diagnostico" not in enviados:
            cards.append({"tipo": "diagnostico", "dados": m})
        if o and (o.get("opcoes") or o.get("encaminhar_humano")):
            cards.append({"tipo": "comparador", "dados": {**o, "oferta_em_foco": oid, "ofertas_liberadas": ctx_gi.get("ofertas_liberadas")}})
        ferramentas.append("listar_ofertas")
    elif acao == "abrir_resumo_contrato":
        idx = contexto_mod.indice_da_oferta(ctx_gi, oid, ids)
        if st.get("plano"):
            cards.append(_card_confirmacao(st))
        elif idx is not None and o and o.get("opcoes"):
            r = tools.confirmar_plano(idx, tool_context=ctx_tool)
            ferramentas.append("confirmar_plano")
            if r.get("encaminhar_humano"):
                cards.append(_card_encaminhado(r.get("motivo") or "a opção exige confirmação com uma pessoa"))
                ferramentas.append("encaminhar_humano")
            elif not r.get("erro"):
                _trace_add(ctx_tool.state, "confirmar_plano", {"indice_opcao": idx, "oferta_id": oid}, f"plano {r.get('caminho')}: parcela {r.get('parcela')}, termina em {r.get('termina_em')}",
                           [], 0, "confirmar")
                cards.append(_card_confirmacao(ctx_tool.state))
    elif acao == "transferir_humano":
        r = tools.encaminhar_humano("pedido do cliente ou sinal de proteção (modo gi)", tool_context=ctx_tool)
        ferramentas.append("encaminhar_humano")
        _trace_add(ctx_tool.state, "encaminhar_humano", {"motivo": r["motivo"]}, "encaminhado a uma pessoa; nenhum produto", [], 0, "encaminhamento")
        cards.append(_card_encaminhado(r["motivo"]))
    elif acao == "mostrar_formas_de_pagar":
        cards.append(_card_formas_de_pagar(ctx_gi))
        if o and o.get("encaminhar_humano") and not st.get("plano"):
            cards.append({"tipo": "encaminhamento", "dados": {"motivo": o["motivo"], "status": "disponivel",
                                                              "texto": "Você pode falar com uma pessoa do time a qualquer momento; ela recebe o contexto desta conversa."}})
    elif acao == "revogar_consentimento":
        tools.registrar_consentimento(False, tool_context=ctx_tool)
        ferramentas.append("registrar_consentimento")
        _trace_add(ctx_tool.state, "registrar_consentimento", {"concedido": False, "via": "acao do agente"}, "consentimento revogado a pedido do cliente", [], 0, "consentimento")
        cards.append({"tipo": "aviso", "dados": {"texto": "Análise desligada. Nenhum dado do seu mês fica em uso; você pode ativar de novo quando quiser.", "contratado": False}})
    elif acao == "registrar_permissao_ampliacao":
        ctx_tool.state["permissao_ampliacao"] = True
        cards.append({"tipo": "aviso", "dados": {"texto": "Permissão para ampliar o limite registrada (simulação). O resumo vem na próxima mensagem.", "contratado": False}})
    elif acao == "mudar_vencimento":
        cards.append({"tipo": "aviso", "dados": {"texto": "A troca de vencimento não está disponível nesta simulação.", "contratado": False}})
    elif acao == "devolver_ao_iai":
        cards.append({"tipo": "aviso", "dados": {"texto": "Assunto fora da fatura: a conversa volta para o ia.i (simulação).", "contratado": False}})
    if acao_chip == "nao_quero":
        ctx_tool.state["recusou_oferta"] = True
        cards.append({"tipo": "aviso", "dados": {"texto": "Registrado. A IA não volta ao assunto até a próxima fatura.", "contratado": False}})
    ctx_tool.state["cards_enviados"] = sorted(enviados | {c["tipo"] for c in cards})
    await _aplicar_delta(runner, sessao, _delta(ctx_tool))
    return cards, ferramentas


def _sugestoes_gi(st, saida: dict, ctx_gi: dict, acao_chip: str | None) -> list[dict]:
    acao = saida.get("acao") or "nenhuma"
    if st.get("encaminhado") or acao == "transferir_humano":
        return []
    if acao == "abrir_resumo_contrato" and st.get("plano"):
        return [{"rotulo": "Avançar um mês (demo)", "acao": "avancar_mes"}, CHIP_PESSOA]
    if acao == "mostrar_oferta":
        return [{"rotulo": "Quero essa opção", "acao": "confirmar"}, {"rotulo": "Prefiro continuar como está", "acao": "nao_quero", "secundario": True}, CHIP_PESSOA]
    if ctx_gi.get("entrada_regular_a_confirmar") and acao == "nenhuma" and not st.get("plano") and not st.get("recusou_oferta"):
        return CHIPS_PIX + [CHIP_PESSOA]
    if acao == "revogar_consentimento":
        return [{"rotulo": "Permitir a análise", "acao": "consentir"}, CHIP_PESSOA]
    return _sugestoes(st, acao_chip)


async def _conversar_gi(runner: Runner, sessao, sessao_id: str, acao: str | None, texto: str, modelo: str | None,
                        gatilho: str | None = None) -> dict:
    st0 = sessao.state
    n_trace = len(st0.get("trace") or [])
    fin0 = dict(st0.get("finops") or {})
    t0 = time.perf_counter()

    # PIX confirmado pelo cliente: o servidor (não o modelo) refaz as contas com contar_pix=True
    if acao in ACOES_ENTRADA_REGULAR and st0.get("consentimento"):
        contar = acao != "nao_contar_pix"
        await _aplicar_delta(runner, sessao, {"contar_pix": contar, "ofertas": None if contar else st0.get("ofertas")})
        sessao = await _obter_sessao(runner, sessao_id)
        if contar:
            err = await _analise_deterministica(runner, sessao, contar_pix=True)
            if err:
                return err
            sessao = await _obter_sessao(runner, sessao_id)

    gatilho = gatilho or _gatilho(sessao.state, acao)
    try:
        r_ctx = await _montar_contexto_sessao(runner, sessao, "conversa", gatilho)
    except capacidade.DadosIndisponiveis as e:
        return _erro("dados_indisponiveis", f"{MENSAGEM_ERRO_DADOS} ({e})")
    sessao = await _obter_sessao(runner, sessao_id)
    ctx_gi, indice, ids = r_ctx["contexto"], r_ctx["indice"], r_ctx["ids"]

    r = await _turno_gi(runner, sessao, ctx_gi, indice, texto, modo="conversa", consentimento=bool(sessao.state.get("consentimento")),
                        modelo_validador=os.environ.get("MODELO_VALIDADOR") or None, texto_cliente=texto)
    if r["erro"] is not None:
        return _resposta_erro_modelo(sessao_id, st0, r["erro"])
    sessao = await _obter_sessao(runner, sessao_id)
    saida = r["saida"] or checagens.mensagem_segura(ctx_gi, "conversa")
    if r.get("nao_enviar"):
        saida = {"mensagens": [], "acao": "nenhuma", "oferta_id": None, "numeros_citados": []}

    # trace das checagens e do validador
    ctx_tool = _ctx(sessao)
    for t in r["tentativas"]:
        ch = t["checagens"]
        _trace_add(ctx_tool.state, "checagens", {"tentativa": t["tentativa"], "acao": ch.get("acao")},
                   "ok" if ch["ok"] else "falhou: " + "; ".join(f"{f['regra']}: {f['motivo']}" for f in ch["falhas"][:4]), [], 0, "checagens",
                   falhas=ch["falhas"])
    for v in r["validacoes"]:
        _registrar_validacao(ctx_tool.state, v, saida, "validador")
    if r.get("entrada_bloqueada"):
        _trace_add(ctx_tool.state, "validador", {"modo": "gi", "aplicado": False}, "não aplicado: entrada bloqueada antes do modelo (recusa fixa em código)", [], 0, "validador")
    if r["mensagem_segura"]:
        _trace_add(ctx_tool.state, "mensagem_segura", {"motivo": r.get("motivo_segura")}, "texto fixo no lugar da resposta do modelo", [], 0, "mensagem_segura")
    ctx_tool.state["turnos_llm"] = int(ctx_tool.state.get("turnos_llm") or 0) + 1
    await _aplicar_delta(runner, sessao, _delta(ctx_tool))
    sessao = await _obter_sessao(runner, sessao_id)

    cards, ferramentas = await _efeitos_da_acao(runner, sessao, saida, ctx_gi, ids, acao)
    sessao = await _obter_sessao(runner, sessao_id)
    st = sessao.state

    # segunda barreira (a mesma do modo tools), mensagem a mensagem: nenhum número sem origem chega ao cliente
    mensagens_txt = []
    rel = {"removidos": [], "termos_bloqueados": [], "substituicoes": 0}
    for m in (saida.get("mensagens") or []):
        if not isinstance(m, str) or not m.strip():
            continue
        novo, rel_m = callbacks.guardiao_texto(m, st.get("numeros_validados"))
        rel["removidos"] += rel_m["removidos"]
        rel["termos_bloqueados"] += rel_m["termos_bloqueados"]
        rel["substituicoes"] += rel_m["substituicoes"]
        mensagens_txt.append(novo)
    guard = {"removidos": [x["frase"] for x in rel["removidos"]], "termos_bloqueados": list(rel["termos_bloqueados"]),
             "substituicoes": int(rel["substituicoes"]), "checagens": (r.get("checagens") or {}).get("falhas", [])}
    hist = _historico_mais(st, texto, " ".join(mensagens_txt) if mensagens_txt else None)
    delta = {"historico_conversa": hist}
    if rel["removidos"] or rel["substituicoes"]:
        g = dict(st.get("guardiao") or {})
        g["removidos"] = list(g.get("removidos") or []) + rel["removidos"]
        g["termos_bloqueados"] = list(g.get("termos_bloqueados") or []) + rel["termos_bloqueados"]
        g["substituicoes"] = int(g.get("substituicoes") or 0) + rel["substituicoes"]
        delta["guardiao"] = g
    await _aplicar_delta(runner, sessao, delta)
    st = (await _obter_sessao(runner, sessao_id)).state
    duracao = int((time.perf_counter() - t0) * 1000)
    val_info = {"chamadas": len(r["validacoes"]), "latencias": [v.get("latencia_ms") for v in r["validacoes"]]}
    ultimo_val = r["validador"] or {}
    return {
        "sessao_id": sessao_id,
        "mensagens": [{"papel": "agente", "texto": m} for m in mensagens_txt],
        "cards": cards,
        "numeros_validados": list(st.get("numeros_validados") or []),
        "guardiao": guard,
        "sugestoes": _sugestoes_gi(st, saida, ctx_gi, acao),
        "acao": saida.get("acao") or "nenhuma",
        "oferta_id": saida.get("oferta_id"),
        "numeros_citados": list(saida.get("numeros_citados") or []),
        "validador": {"ativo": validador.ativo(), "aprovado": ultimo_val.get("aprovado"), "violacoes": list(ultimo_val.get("violacoes") or []),
                      "orientacao": ultimo_val.get("orientacao"), "erro": ultimo_val.get("erro"), "regeneracoes": r["regeneracoes"],
                      "mensagem_segura": r["mensagem_segura"], "motivo_mensagem_segura": r.get("motivo_segura"),
                      **({"motivo": "entrada bloqueada antes do modelo: recusa fixa em código, validador não aplicado"} if r.get("entrada_bloqueada") else {})},
        "entrada_bloqueada": bool(r.get("entrada_bloqueada")),
        "gatilho": gatilho, "modo_conversa": "gi",
        "finops": _finops_turno(st, fin0, duracao, ferramentas, val_info),
        "simulado": True,
    }


# ------------------------------------------------------------------ modo tools: validador depois do guardião
def _acao_do_turno_tools(chamadas: list[str], cards: list[dict], texto: str, ctx_gi: dict | None = None) -> tuple[str, str | None]:
    """Ação do formato da Gi para um turno do modo tools (para o validador e para a API). Só é mostrar_oferta quando o
    texto cita a oferta (parcela, custo total ou prazo): o comparador na tela sozinho não é uma oferta na fala."""
    if "confirmar_plano" in chamadas:
        return "abrir_resumo_contrato", None
    if "encaminhar_humano" in chamadas or any(c["tipo"] == "encaminhamento" and c["dados"].get("status") == "encaminhado" for c in cards):
        return "transferir_humano", None
    if "registrar_consentimento" in chamadas:
        return "revogar_consentimento", None
    chaves_texto = checagens.chaves(texto or "")
    for of in (ctx_gi or {}).get("ofertas_liberadas") or []:
        for campo in ("parcela", "custo_total", "prazo"):
            if of.get(campo) and checagens.chaves(str(of[campo])) & chaves_texto:
                return "mostrar_oferta", of.get("id")
    if "listar_ofertas" in chamadas and (ctx_gi or {}).get("ofertas_liberadas"):
        return "mostrar_oferta", (ctx_gi or {})["ofertas_liberadas"][0].get("id")
    return "nenhuma", None


async def _validar_modo_tools(runner: Runner, sessao, sessao_id: str, texto_final: str, chamadas: list[str], cards: list[dict], texto_cliente: str,
                              acao_chip: str | None) -> tuple[str, list[dict], dict]:
    """Validador sobre o texto do modo tools (embrulhado no JSON da Gi). Uma regeneração; depois mensagem segura."""
    st = sessao.state
    try:
        r_ctx = await _montar_contexto_sessao(runner, sessao, "conversa", "pergunta_cliente", manter_ofertas=True)
    except capacidade.DadosIndisponiveis:
        return texto_final, cards, {"ativo": True, "aprovado": None, "erro": "contexto indisponível", "violacoes": [], "regeneracoes": 0, "mensagem_segura": False}
    ctx_gi = r_ctx["contexto"]
    acao, oid = _acao_do_turno_tools(chamadas, cards, texto_final, ctx_gi)
    if acao == "abrir_resumo_contrato" and ctx_gi.get("ofertas_liberadas"):
        i = st.get("opcao_confirmada")
        ofs = ctx_gi["ofertas_liberadas"]
        oid = ofs[i]["id"] if isinstance(i, int) and 0 <= i < len(ofs) else ofs[0]["id"]
    saida = validador.saida_json_de_texto(texto_final, acao, oid)
    sessao = await _obter_sessao(runner, sessao_id)
    r = await validador.validar_async(saida, ctx_gi, _historico_mais(sessao.state, texto_cliente, None), modelo=os.environ.get("MODELO_VALIDADOR") or None)
    ctx_tool = _ctx(sessao)
    _registrar_validacao(ctx_tool.state, r, saida, "validador")
    await _aplicar_delta(runner, sessao, _delta(ctx_tool))
    sessao = await _obter_sessao(runner, sessao_id)
    regeneracoes, segura, motivo = 0, False, None
    latencias_val = [r.get("latencia_ms")]
    if r.get("aprovado") is False:
        if r.get("r8"):
            segura, motivo = True, "validador: R8"
        else:
            regeneracoes = 1
            try:
                textos = await _modelo(runner, sessao, validador.texto_correcao(r, em_json=False)
                                       + "\nNão chame ferramentas de novo; responda em texto corrido, sem JSON nem bloco de código, no máximo 3 mensagens curtas.",
                                       "regeneracao")
                sessao = await _obter_sessao(runner, sessao_id)
                novo = "\n\n".join(t.strip() for t in textos if t.strip()).strip()
                novo, _rel = callbacks.guardiao_texto(novo, sessao.state.get("numeros_validados"))
                saida2 = validador.saida_json_de_texto(novo, acao, oid)
                r2 = await validador.validar_async(saida2, ctx_gi, _historico_mais(sessao.state, texto_cliente, None), modelo=os.environ.get("MODELO_VALIDADOR") or None)
                ctx_tool = _ctx(sessao)
                _registrar_validacao(ctx_tool.state, r2, saida2, "validador")
                await _aplicar_delta(runner, sessao, _delta(ctx_tool))
                latencias_val.append(r2.get("latencia_ms"))
                if r2.get("aprovado") is False:
                    segura, motivo = True, "validador: reprovado de novo"
                    r = r2
                else:
                    texto_final, r = novo, r2
            except Exception as e:
                callbacks._log("erro_modelo", sessao=sessao_id, erro=type(e).__name__, detalhe=str(e)[:200])
                segura, motivo = True, f"regeneração falhou: {type(e).__name__}"
    if segura:
        sessao = await _obter_sessao(runner, sessao_id)
        humano = motivo == "validador: R8"
        ms = checagens.mensagem_segura(ctx_gi, "conversa", humano=humano)
        texto_final = "\n\n".join(ms["mensagens"])
        # o card de confirmação fica: o plano já foi registrado pela ferramenta (só o texto do modelo é trocado)
        cards = [c for c in cards if c["tipo"] in ("diagnostico", "confirmacao")]
        if humano:
            ctx_tool = _ctx(sessao)
            enc = tools.encaminhar_humano("sinal de proteção (validador R8)", tool_context=ctx_tool)
            await _aplicar_delta(runner, sessao, _delta(ctx_tool))
            cards.append(_card_encaminhado(enc["motivo"]))
        else:
            cards.append(_card_formas_de_pagar(ctx_gi))
        ctx_tool = _ctx(await _obter_sessao(runner, sessao_id))
        _trace_add(ctx_tool.state, "mensagem_segura", {"motivo": motivo}, "texto fixo no lugar da resposta do modelo", [], 0, "mensagem_segura")
        await _aplicar_delta(runner, sessao, _delta(ctx_tool))
    if segura:
        acao, oid = ("transferir_humano" if motivo == "validador: R8" else "mostrar_formas_de_pagar"), None
    info = {"ativo": True, "aprovado": r.get("aprovado"), "violacoes": list(r.get("violacoes") or []), "orientacao": r.get("orientacao"),
            "erro": r.get("erro"), "regeneracoes": regeneracoes, "mensagem_segura": segura, "motivo_mensagem_segura": motivo,
            "chamadas": len(latencias_val), "latencias": latencias_val, "acao": acao, "oferta_id": oid}
    return texto_final, cards, info


async def conversar_async(sessao_id: str, cliente_id: str, anomes: int, texto_ou_acao: str, valor: int | None = None,
                          modelo: str | None = None, gatilho: str | None = None, modo: str | None = None,
                          contar_pix: bool | None = None) -> dict:
    """Uma rodada da conversa. Devolve o formato de POST /api/mensagem (+ finops do turno).

    gatilho/modo/contar_pix são opcionais (o servidor os passa no modo gi): gatilho força o gatilho do prompt da Gi;
    contar_pix=True refaz as contas com o PIX como renda antes do turno; modo só aceita 'conversa' aqui.
    """
    modo_gi_forcado = gatilho if gatilho in contexto_mod.GATILHOS else None
    modo = modo_conversa()
    runner = criar_runner(modelo or (_REGISTRO.get(sessao_id) or {}).get("modelo"), modo)
    sessao = await _obter_sessao(runner, sessao_id, cliente_id)
    if sessao is None:
        r = await criar_sessao_async(cliente_id, anomes, sessao_id=sessao_id, modelo=nome_do_modelo(runner))
        if r.get("erro"):
            return r
        sessao = await _obter_sessao(runner, sessao_id, cliente_id)
    acao, texto = _texto_do_usuario(texto_ou_acao, valor)
    if acao in ACOES_SEM_LLM:
        return await _escolher_pagamento(runner, sessao, acao, valor)
    if not texto:
        return _erro("mensagem_vazia", "Não entendi. Pode repetir?")
    if len(texto) > 2000:
        texto = texto[:2000]
    if contar_pix is not None and acao not in ACOES_ENTRADA_REGULAR:
        acao = "confirmar_entrada_regular" if contar_pix else "nao_contar_pix"
    if sessao.state.get("modo_conversa") != modo:
        await _aplicar_delta(runner, sessao, {"modo_conversa": modo})
        sessao = await _obter_sessao(runner, sessao_id, cliente_id)
    if modo == "gi":
        return await _conversar_gi(runner, sessao, sessao_id, acao, texto, modelo, gatilho=modo_gi_forcado)

    st0 = sessao.state
    n_trace = len(st0.get("trace") or [])
    fin0 = dict(st0.get("finops") or {})
    await _aplicar_delta(runner, sessao, {"guardiao": {"removidos": [], "termos_bloqueados": [], "substituicoes": 0}})
    t0 = time.perf_counter()
    try:
        textos = await _modelo(runner, sessao, texto, "agente")
    except Exception as e:  # modelo indisponível, credencial, quota: a demo cai no caminho sem LLM
        return _resposta_erro_modelo(sessao_id, st0, e)
    duracao = int((time.perf_counter() - t0) * 1000)

    sessao = await _obter_sessao(runner, sessao_id, cliente_id)
    st = sessao.state
    texto_final = "\n\n".join(t.strip() for t in textos if t.strip()).strip()
    texto_final, rel = callbacks.guardiao_texto(texto_final, st.get("numeros_validados"))   # segunda passada: cobre o texto juntado
    novos = [t for t in (st.get("trace") or [])[n_trace:]]
    chamadas = [t["ferramenta"] for t in novos if not t.get("bloqueada") and not t.get("llm")
                and t["ferramenta"] not in ("contexto.montar", "validador", "checagens", "mensagem_segura", "bloqueio_entrada")]
    bloqueadas = any(t.get("bloqueada") for t in novos)
    entrada_bloqueada = any(t.get("ferramenta") == "bloqueio_entrada" for t in novos)   # injeção: recusa fixa, sem validador
    delta = {}
    if acao == "nao_quero":
        delta["recusou_oferta"] = True
    cards = _cards(st, acao, chamadas, bloqueadas)
    guard = dict(st.get("guardiao") or {})
    if rel["removidos"] or rel["substituicoes"]:
        guard["removidos"] = list(guard.get("removidos") or []) + rel["removidos"]
        guard["termos_bloqueados"] = list(guard.get("termos_bloqueados") or []) + rel["termos_bloqueados"]
        guard["substituicoes"] = int(guard.get("substituicoes") or 0) + rel["substituicoes"]
        delta["guardiao"] = guard
    await _aplicar_delta(runner, sessao, delta)
    sessao = await _obter_sessao(runner, sessao_id, cliente_id)

    val_info = {"ativo": False, "aprovado": None, "violacoes": [], "regeneracoes": 0, "mensagem_segura": False, "chamadas": 0, "latencias": []}
    if "confirmar_plano" in chamadas and st.get("plano"):
        # Plano confirmado pela ferramenta: o texto é fixo, em código, como no modo gi (o servidor resolve 'confirmar' sem modelo).
        # O texto do modelo neste turno cita números do núcleo que o contexto da Gi não expõe (teto do cartão) e o validador
        # o reprovava por R1/R19; o plano já está registrado, então nada do modelo precisa chegar ao cliente aqui.
        texto_final = TEXTO_PLANO_CONFIRMADO
        i_conf = st.get("opcao_confirmada")
        oid_conf = next((k for k, v in (st.get("gi_ids") or {}).items() if v == i_conf), None)   # id da Gi (con_01...) da opção confirmada
        val_info = {**val_info, "ativo": validador.ativo(), "acao": "abrir_resumo_contrato", "oferta_id": oid_conf,
                    "motivo": "plano confirmado pela ferramenta: texto fixo em código; só as checagens em código"}
        ctx_tool = _ctx(sessao)
        _trace_add(ctx_tool.state, "validador", {"modo": "tools", "aplicado": False}, "não aplicado: texto fixo do plano confirmado (em código)", [], 0, "validador")
        await _aplicar_delta(runner, sessao, _delta(ctx_tool))
        sessao = await _obter_sessao(runner, sessao_id, cliente_id)
    elif entrada_bloqueada:
        val_info = {**val_info, "ativo": validador.ativo(), "acao": "nenhuma", "oferta_id": None,
                    "motivo": "entrada bloqueada antes do modelo: recusa fixa em código, validador não aplicado"}
        ctx_tool = _ctx(sessao)
        _trace_add(ctx_tool.state, "validador", {"modo": "tools", "aplicado": False}, "não aplicado: entrada bloqueada antes do modelo (recusa fixa em código)", [], 0, "validador")
        await _aplicar_delta(runner, sessao, _delta(ctx_tool))
        sessao = await _obter_sessao(runner, sessao_id, cliente_id)
    elif validador.ativo() and texto_final:
        texto_final, cards, val_info = await _validar_modo_tools(runner, sessao, sessao_id, texto_final, chamadas, cards, texto, acao)
        sessao = await _obter_sessao(runner, sessao_id, cliente_id)
    st = sessao.state
    await _aplicar_delta(runner, sessao, {"cards_enviados": sorted(set(st.get("cards_enviados") or []) | {c["tipo"] for c in cards}),
                                          "historico_conversa": _historico_mais(st, texto, texto_final), "turnos_llm": int(st.get("turnos_llm") or 0) + 1})
    st = (await _obter_sessao(runner, sessao_id, cliente_id)).state
    if val_info.get("acao"):
        acao_turno, oid_turno = val_info["acao"], val_info.get("oferta_id")
    else:
        acao_turno, oid_turno = _acao_do_turno_tools(chamadas, cards, texto_final)
    return {
        "sessao_id": sessao_id,
        "mensagens": _mensagens_do_agente(texto_final or callbacks.RESPOSTA_VAZIA),
        "cards": cards,
        "numeros_validados": list(st.get("numeros_validados") or []),
        "guardiao": {"removidos": [r["frase"] for r in guard.get("removidos") or []], "termos_bloqueados": list(guard.get("termos_bloqueados") or []),
                     "substituicoes": int(guard.get("substituicoes") or 0)},
        "sugestoes": _sugestoes(st, acao),
        "acao": acao_turno, "oferta_id": oid_turno, "numeros_citados": [n["texto"] for n in checagens.extrair(texto_final)],
        "validador": {k: v for k, v in val_info.items() if k not in ("chamadas", "latencias", "acao", "oferta_id")},
        "modo_conversa": "tools", "entrada_bloqueada": entrada_bloqueada,
        "finops": _finops_turno(st, fin0, duracao, chamadas, val_info),
        "simulado": True,
    }


def conversar(sessao_id: str, cliente_id: str, anomes: int, texto_ou_acao: str, valor: int | None = None, modelo: str | None = None,
              gatilho: str | None = None, modo: str | None = None, contar_pix: bool | None = None) -> dict:
    return _executar(conversar_async(sessao_id, cliente_id, anomes, texto_ou_acao, valor, modelo, gatilho, modo, contar_pix))


async def atualizar_estado_async(sessao_id: str, delta: dict, modelo: str | None = None) -> dict:
    """Gancho do servidor: espelha no state do agente o que foi decidido em código (plano, recusa, encaminhamento, contar_pix)."""
    runner = _runner_da_sessao(sessao_id, modelo)
    sessao = await _obter_sessao(runner, sessao_id)
    if sessao is None:
        return _erro("sessao_inexistente", MENSAGEM_ERRO_DADOS)
    permitidas = ("plano", "recusou_oferta", "encaminhado", "contar_pix", "escolha_pagamento", "historico_contratacoes", "mes_simulado",
                  "ciclos", "permissao_ampliacao", "historico_conversa", "cards_enviados", "consentimento", "consentimento_registro", "motor", "ofertas")
    d = {k: v for k, v in (delta or {}).items() if k in permitidas}
    if d:
        await _aplicar_delta(runner, sessao, d)
    return {"ok": True, "chaves": sorted(d)}


def atualizar_estado(sessao_id: str, delta: dict, modelo: str | None = None) -> dict:
    return _executar(atualizar_estado_async(sessao_id, delta, modelo))


# ------------------------------------------------------------------ insight pelo LLM (modo gi; só com INSIGHT_COM_LLM=true ou nos evals)
def _insight_de_saida(saida: dict) -> dict:
    bp = saida.get("botao_primario") if isinstance(saida.get("botao_primario"), dict) else None
    mapa = {"abrir_chat": "ver_opcoes", "ver_formas_de_pagar": "ver_formas_de_pagar", "nenhuma": None}
    botao = None
    if bp and mapa.get(bp.get("acao")):
        botao = {"rotulo": bp.get("rotulo") or "Ver no ia.i", "acao": mapa[bp.get("acao")]}
    return {"estado": "gi", "texto": saida.get("texto") or "", "botao": botao, "botao_secundario": saida.get("botao_secundario"),
            "numeros_citados": list(saida.get("numeros_citados") or []), "mensagem_segura": bool(saida.get("mensagem_segura"))}


async def insight_gi_async(sessao_id: str, gatilho: str = "fechamento", modelo: str | None = None, modo: str | None = None) -> dict:
    """Insight da aba de cartões pelo prompt da Gi (modo insight). Uma mensagem proativa por dia (não envia a segunda)."""
    runner = _runner_da_sessao(sessao_id, modelo, "gi")
    sessao = await _obter_sessao(runner, sessao_id)
    if sessao is None:
        return _erro("sessao_inexistente", MENSAGEM_ERRO_DADOS)
    st0 = sessao.state
    fin0 = dict(st0.get("finops") or {})
    t0 = time.perf_counter()
    try:
        r_ctx = await _montar_contexto_sessao(runner, sessao, "insight", gatilho)
    except capacidade.DadosIndisponiveis as e:
        return _erro("dados_indisponiveis", f"{MENSAGEM_ERRO_DADOS} ({e})")
    sessao = await _obter_sessao(runner, sessao_id)
    ja_hoje = bool(st0.get("proativa_enviada_em")) and st0.get("proativa_enviada_em") == st0.get("data_simulada")
    r = await _turno_gi(runner, sessao, r_ctx["contexto"], r_ctx["indice"], "Gere o texto do modo insight para a aba de cartões.",
                        modo="insight", consentimento=bool(sessao.state.get("consentimento")), modelo_validador=os.environ.get("MODELO_VALIDADOR") or None,
                        proativa_ja_enviada_hoje=ja_hoje)
    if r["erro"] is not None:
        return _resposta_erro_modelo(sessao_id, st0, r["erro"])
    sessao = await _obter_sessao(runner, sessao_id)
    ctx_tool = _ctx(sessao)
    for t in r["tentativas"]:
        ch = t["checagens"]
        _trace_add(ctx_tool.state, "checagens", {"tentativa": t["tentativa"], "acao": ch.get("acao"), "modo": "insight"},
                   "ok" if ch["ok"] else "falhou: " + "; ".join(f"{f['regra']}: {f['motivo']}" for f in ch["falhas"][:4]), [], 0, "checagens", falhas=ch["falhas"])
    for v in r["validacoes"]:
        _registrar_validacao(ctx_tool.state, v, r["saida"] or {}, "validador")
    if not r.get("nao_enviar"):
        ctx_tool.state["proativa_enviada_em"] = st0.get("data_simulada")
    await _aplicar_delta(runner, sessao, _delta(ctx_tool))
    st = (await _obter_sessao(runner, sessao_id)).state
    saida = r["saida"] or checagens.mensagem_segura(r_ctx["contexto"], "insight")
    ins = _insight_de_saida(saida)
    if r.get("nao_enviar"):
        ins = {"estado": "nao_enviar", "texto": "", "botao": None, "numeros_citados": [], "mensagem_segura": False}
    ultimo_val = r["validador"] or {}
    return {"insight": ins, "saida": saida, "checagens": r["checagens"], "nao_enviar": bool(r.get("nao_enviar")),
            "validador": {"ativo": validador.ativo(), "aprovado": ultimo_val.get("aprovado"), "violacoes": list(ultimo_val.get("violacoes") or []),
                          "regeneracoes": r["regeneracoes"], "mensagem_segura": r["mensagem_segura"]},
            "numeros_validados": list(st.get("numeros_validados") or []),
            "finops": _finops_turno(st, fin0, int((time.perf_counter() - t0) * 1000), [], {"chamadas": len(r["validacoes"]), "latencias": [v.get("latencia_ms") for v in r["validacoes"]]}),
            "simulado": True}


def insight_gi(sessao_id: str, gatilho: str = "fechamento", modelo: str | None = None, modo: str | None = None) -> dict:
    return _executar(insight_gi_async(sessao_id, gatilho, modelo, modo))


# ------------------------------------------------------------------ evals: o agente da Gi sobre um contexto pronto (sem núcleo)
async def gerar_gi_async(contexto_pronto: dict, turnos: list[str], *, modelo: str | None = None, validar: bool | None = None,
                         modelo_validador: str | None = None) -> list[dict]:
    """Roda o agente da Gi numa sessão descartável com um contexto ilustrativo (evals/golden_gi.json). Um resultado por turno."""
    modo = contexto_pronto.get("modo") or "conversa"
    runner = criar_runner(modelo, "gi")
    indice = contexto_mod.indice_de(contexto_pronto)
    sid = f"eval-{uuid.uuid4().hex[:10]}"
    estado = {"cliente_id": "eval", "anomes": 0, "consentimento": bool(contexto_pronto.get("consentimento")), "numeros_validados": contexto_mod.numeros_de(indice),
              "trace": [], "finops": {"chamadas_llm": 0, "chamadas_validador": 0, "tokens_entrada": 0, "tokens_saida": 0, "tokens_entrada_validador": 0, "tokens_saida_validador": 0, "latencias_ms": []},
              "historico_conversa": [], "modo_conversa": "gi", agente_gi.CHAVE_CONTEXTO: prompt_gi.contexto_json_texto(contexto_pronto)}
    sessao = await runner.session_service.create_session(app_name=APP, user_id="eval", session_id=sid, state=estado)
    out = []
    try:
        for texto in turnos:
            sessao = await runner.session_service.get_session(app_name=APP, user_id="eval", session_id=sid)
            fin0 = dict(sessao.state.get("finops") or {})
            t0 = time.perf_counter()
            r = await _turno_gi(runner, sessao, contexto_pronto, indice, texto, modo=modo, consentimento=bool(contexto_pronto.get("consentimento")),
                                modelo_validador=modelo_validador, validar=validar, texto_cliente=texto)
            sessao = await runner.session_service.get_session(app_name=APP, user_id="eval", session_id=sid)
            saida_txt = " ".join(checagens.textos_da_saida(r["saida"], modo)) if r["saida"] else ""
            await _aplicar_delta(runner, sessao, {"historico_conversa": _historico_mais(sessao.state, texto, saida_txt or None)})
            sessao = await runner.session_service.get_session(app_name=APP, user_id="eval", session_id=sid)
            fin = _finops_turno(sessao.state, fin0, int((time.perf_counter() - t0) * 1000), [],
                                {"chamadas": len(r["validacoes"]), "latencias": [v.get("latencia_ms") for v in r["validacoes"]]})
            out.append({**{k: v for k, v in r.items() if k != "erro"}, "erro": None if r["erro"] is None else f"{type(r['erro']).__name__}: {str(r['erro'])[:200]}",
                        "finops": fin, "entrada": texto})
            if r["erro"] is not None:
                break
    finally:
        try:
            await runner.session_service.delete_session(app_name=APP, user_id="eval", session_id=sid)
        except Exception:
            pass
    return out


def gerar_gi(contexto_pronto: dict, turnos: list[str], *, modelo: str | None = None, validar: bool | None = None, modelo_validador: str | None = None) -> list[dict]:
    return _executar(gerar_gi_async(contexto_pronto, turnos, modelo=modelo, validar=validar, modelo_validador=modelo_validador))


# ------------------------------------------------------------------ /api/avancar-mes (sem LLM)
async def avancar_mes_async(sessao_id: str, modelo: str | None = None) -> dict:
    runner = _runner_da_sessao(sessao_id, modelo)
    sessao = await _obter_sessao(runner, sessao_id)
    if sessao is None:
        return _erro("sessao_inexistente", MENSAGEM_ERRO_DADOS)
    st = sessao.state
    plano = st.get("plano")
    if not plano:
        return _erro("sem_plano", "Ainda não há plano confirmado para acompanhar.")
    if plano.get("encerrado"):
        return _erro("plano_encerrado", "O plano já encerrou: três faturas inteiras seguidas.")
    seguinte = calendario.anomes_soma(int(st.get("mes_simulado") or st["anomes"]), 1)
    t0 = time.perf_counter()
    c = acompanhar.ciclo(tools.fonte(), st["cliente_id"], plano, seguinte, policy.registro_taxas())
    ctx = _ctx(sessao)
    tools.registrar_numeros(ctx.state, c["numeros"])
    n0 = len(st.get("numeros_validados") or [])
    hist = list(st.get("historico_contratacoes") or [])
    if hist and not c["paga_inteira"]:
        hist[-1] = {**hist[-1], "rolou_depois": True}     # pedalada depois do aceite: reincidência (trava em ofertas)
    ctx.state.update({"plano": c["plano"], "mes_simulado": seguinte, "ciclos": list(st.get("ciclos") or []) + [{k: v for k, v in c.items() if k not in ("plano", "numeros", "origem")}],
                      "historico_contratacoes": hist})
    for k in ("plano", "mes_simulado", "ciclos", "historico_contratacoes"):
        ctx.state.alteradas.add(k)
    _trace_add(ctx.state, "acompanhar.ciclo", {"anomes": seguinte, "fonte_mes": c["fonte_mes"], "llm": False}, c["frase"],
               list(ctx.state["numeros_validados"])[n0:], int((time.perf_counter() - t0) * 1000), "acompanhamento")
    await _aplicar_delta(runner, sessao, _delta(ctx))
    dados = {k: v for k, v in c.items() if k not in ("numeros",)}
    return {"anomes": c["anomes"], "fatura": c["fatura"], "paga_inteira": c["paga_inteira"], "ciclos_ok": c["ciclos_ok"], "encerrado": c["encerrado"],
            "mensagem": c["frase"], "cards": [{"tipo": "acompanhamento", "dados": dados}],
            "numeros_validados": list(ctx.state["numeros_validados"]), "simulado": True}


def avancar_mes(sessao_id: str, modelo: str | None = None) -> dict:
    return _executar(avancar_mes_async(sessao_id, modelo))


# ------------------------------------------------------------------ trace, painel, saúde
async def trace_async(sessao_id: str, modelo: str | None = None) -> list[dict]:
    sessao = await _obter_sessao(_runner_da_sessao(sessao_id, modelo), sessao_id)
    return list((sessao.state.get("trace") if sessao else None) or [])


def trace(sessao_id: str, modelo: str | None = None) -> list[dict]:
    return _executar(trace_async(sessao_id, modelo))


def custo_estimado(finops: dict | None, modelo: str | None = None, modelo_validador: str | None = None) -> dict:
    """Custo em USD de um bloco finops (sessão ou turno) pelo preço com fonte de config/finops.yaml.

    Devolve {usd, preco_fonte, moeda, modelo, modelo_validador, vigencia, por_papel, motivo}. usd e preco_fonte ficam None
    quando o preço de algum modelo usado não tem `status: confirmada` (DESCONHECIDO em vez de chute); zero chamadas => 0.0.
    por_papel usa finops.chamadas_por_papel (agente | validador | regeneracao | insight) quando o runtime o gravou; o total
    vem de cabe_core.finops.custo_por_papel (agente + validador), a mesma conta do painel.
    """
    fin = dict(finops or {})
    m = modelo or agent_mod.modelo_configurado()
    mv = modelo_validador or validador.modelo_configurado()
    c = finops_core.custo_por_papel(fin, m, mv)
    por_papel: dict[str, float | None] = {}
    for papel, reg in (fin.get("chamadas_por_papel") or {}).items():
        reg = reg if isinstance(reg, dict) else {}
        mm = reg.get("modelo") or (mv if papel == "validador" else m)
        por_papel[papel] = finops_core.custo_usd(int(reg.get("tokens_entrada") or 0), int(reg.get("tokens_saida") or 0), mm)["custo_usd"]
    total = c["total"]["custo_usd"]
    fontes = [p["preco_fonte"] for p in (c["agente"], c["validador"]) if p.get("preco_confirmado")]
    fonte = "; ".join(dict.fromkeys(fontes)) if total is not None and fontes else None
    motivo = None
    if total is None:
        faltam = [p["modelo"] for p in (c["agente"], c["validador"]) if not p.get("preco_confirmado")]
        motivo = f"preço sem status 'confirmada' em config/finops.yaml para: {', '.join(dict.fromkeys(faltam))}"
    return {"usd": total, "preco_fonte": fonte, "moeda": "USD", "modelo": m, "modelo_validador": mv,
            "vigencia": c["agente"].get("vigencia") if total is not None else None, "por_papel": por_papel, "motivo": motivo}


def finops_de(state) -> dict:
    """FinOps da sessão no state do agente: chamadas e tokens por papel, latências, custo em USD (config/finops.yaml)."""
    f = state.get("finops") or {}
    p50, p95 = callbacks.p50_p95(list(f.get("latencias_ms") or []))
    vp50, vp95 = callbacks.p50_p95([x for x in (f.get("latencias_validador_ms") or []) if isinstance(x, int)])
    modelo, mv = agent_mod.modelo_configurado(), validador.modelo_configurado()
    custo = finops_core.custo_por_papel(f, modelo, mv)
    preco = finops_core.preco_de(modelo)
    total = custo["total"]["custo_usd"]
    return {"chamadas_llm": int(f.get("chamadas_llm", 0)), "chamadas_validador": int(f.get("chamadas_validador", 0)),
            "tokens_entrada": int(f.get("tokens_entrada", 0)), "tokens_saida": int(f.get("tokens_saida", 0)),
            "tokens_entrada_validador": int(f.get("tokens_entrada_validador", 0) or 0), "tokens_saida_validador": int(f.get("tokens_saida_validador", 0) or 0),
            "latencia_p50_ms": p50, "latencia_p95_ms": p95, "validador_latencia_p50_ms": vp50, "validador_latencia_p95_ms": vp95,
            "custo_estimado": total, "custo_acumulado_sessao_usd": total, "moeda": "USD",
            "custo_agente_usd": custo["agente"]["custo_usd"], "custo_validador_usd": custo["validador"]["custo_usd"], "por_papel": custo,
            "chamadas_por_papel": {p: {**reg, "custo_usd": custo_estimado(f, modelo, mv)["por_papel"].get(p)}
                                   for p, reg in (f.get("chamadas_por_papel") or {}).items()},
            "entradas_bloqueadas": int(state.get("entradas_bloqueadas") or 0),
            "preco_fonte": preco["fonte"], "vigencia": preco["vigencia"], "preco_status": preco["status"],
            "projecao_piloto": finops_core.projecao(total),
            "modelo": modelo, "modelo_validador": mv, "modo_conversa": state.get("modo_conversa") or modo_conversa(),
            "nota": ("custo em USD pelo preço com fonte em config/finops.yaml (agente + validador); acompanhamento mensal sem LLM"
                     if total is not None else "custo_estimado fica null: preço do modelo a conferir em config/finops.yaml")}


async def painel_async(sessao_id: str, modelo: str | None = None) -> dict:
    sessao = await _obter_sessao(_runner_da_sessao(sessao_id, modelo), sessao_id)
    if sessao is None:
        return _erro("sessao_inexistente", MENSAGEM_ERRO_DADOS)
    st = sessao.state
    ano = (int(st["anomes"]) // 100) * 100 + 12
    # comparativo "2025 real" lê 12 faturas: só com consentimento (regra 3); sem ele o painel sai zerado.
    hist = fatura_mod.historico(tools.fonte(), st["cliente_id"], ano, n=12) if st.get("consentimento") else []
    j = painel_mod.juri(dict(motor=st.get("motor"), ofertas=st.get("ofertas"), plano=st.get("plano"), ciclos=st.get("ciclos") or [],
                             historico_faturas=hist, numeros_validados=st.get("numeros_validados") or []))
    j = {k: v for k, v in j.items() if k != "numeros"}
    j["simulado_lista"] = j.pop("simulado", [])
    j["simulado"] = True
    j["finops"] = finops_de(st)
    j["guardiao"] = st.get("guardiao")
    j["validador"] = {"ativo": validador.ativo(), "modelo": validador.modelo_configurado(), "reprovacoes": list(st.get("validador_reprovacoes") or []),
                      "chamadas": int((st.get("finops") or {}).get("chamadas_validador", 0))}
    j["modo_conversa"] = st.get("modo_conversa") or modo_conversa()
    j["consentimento"] = {"concedido": bool(st.get("consentimento")), "registro": st.get("consentimento_registro")}
    return j


def painel(sessao_id: str, modelo: str | None = None) -> dict:
    return _executar(painel_async(sessao_id, modelo))


def saude() -> dict:
    try:
        nome = tools.fonte().nome
    except Exception as e:  # sem CSV/BigQuery
        return {"ok": False, "dados": None, "modelo": agent_mod.modelo_configurado(), "erro": str(e)[:200]}
    return {"ok": True, "dados": nome, "modelo": agent_mod.modelo_configurado(), "app": APP, **configuracao()}


def estado_bruto(sessao_id: str, modelo: str | None = None) -> dict:
    """Só para depuração e testes: o estado inteiro da sessão."""
    async def _f():
        s = await _obter_sessao(_runner_da_sessao(sessao_id, modelo), sessao_id)
        return dict(s.state) if s else {}
    return _executar(_f())


if __name__ == "__main__":   # uso rápido: uv run python -m cabe_no_bolso.runtime <cliente_id> <anomes> "texto"
    import sys

    cid, am = sys.argv[1], int(sys.argv[2])
    s = criar_sessao(cid, am)
    print(json.dumps(consentir(s["sessao_id"], True)["insight"], ensure_ascii=False))
    for msg in sys.argv[3:] or ["ver_opcoes"]:
        print(json.dumps(conversar(s["sessao_id"], cid, am, msg), ensure_ascii=False, default=str)[:3000])
