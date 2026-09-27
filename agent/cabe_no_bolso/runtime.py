"""Runtime do agente: Runner + InMemorySessionService, com a conversa no formato de POST /api/mensagem.

Interface estável para o servidor (server/main.py):
  criar_runner(modelo=None) -> Runner
  conversar(sessao_id, cliente_id, anomes, texto_ou_acao, valor=None, modelo=None) -> dict   # /api/mensagem
  criar_sessao(cliente_id, anomes, persona=None, sessao_id=None, simulacao=None) -> dict      # /api/sessao (sem LLM)
  consentir(sessao_id, concedido) -> dict                                                     # /api/consentimento (sem LLM)
  avancar_mes(sessao_id) -> dict                                                              # /api/avancar-mes (sem LLM)
  trace(sessao_id) -> list; painel(sessao_id) -> dict; saude() -> dict
Todas têm versão *_async; as síncronas usam asyncio.run (chamar de thread sem loop, como endpoints sync do FastAPI).

Onde o LLM entra: só em conversar() com ações conversacionais ou texto livre. Pagar mínimo/outro valor, consentimento,
insight do cartão, acompanhamento e painel são determinísticos (cabe_core direto), como pede docs/05 (princípio 14).
Sessão em memória: deploy com 1 instância (--min-instances=1 --max-instances=1 --session-affinity, 1 worker).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from cabe_core import acompanhar, calendario, capacidade, fatura as fatura_mod, painel as painel_mod
from cabe_core.dinheiro import brl, coletar_numeros
from cabe_no_bolso import agent as agent_mod, callbacks, policy, tools

APP = agent_mod.NOME
MENSAGEM_ERRO_DADOS = "Não consegui ler seu extrato agora. Posso tentar de novo ou te passar para uma pessoa."
MENSAGEM_ERRO_MODELO = "Não consegui responder agora. Posso tentar de novo ou te passar para uma pessoa."
ACOES = {
    "ver_opcoes": "Ver opções.",
    "consigo_pagar": "Consigo pagar minha fatura?",
    "por_que_alta": "Por que minha fatura veio tão alta?",
    "confirmar": "Sim, quero essa opção. Pode confirmar.",
    "falar_com_pessoa": "Quero falar com uma pessoa.",
    "nao_quero": "Não quero isso. Prefiro continuar com o que escolhi.",
    "pagar_minimo": "Vou pagar só o mínimo.",
    "pagar_outro_valor": "Vou pagar outro valor.",
}
ACOES_SEM_LLM = ("pagar_minimo", "pagar_outro_valor")
CHIP_PESSOA = {"rotulo": "Falar com uma pessoa", "acao": "falar_com_pessoa", "secundario": True}

_RUNNERS: dict[str, Runner] = {}
_REGISTRO: dict[str, dict] = {}     # sessao_id -> {cliente_id, modelo}


# ------------------------------------------------------------------ infraestrutura
def criar_runner(modelo: str | None = None) -> Runner:
    """Um Runner por modelo (InMemorySessionService próprio). Reutilizado entre chamadas."""
    nome = modelo or agent_mod.modelo_configurado()
    if nome not in _RUNNERS:
        agente = agent_mod.root_agent if nome == agent_mod.root_agent.model else agent_mod.criar_agente(nome)
        _RUNNERS[nome] = Runner(app_name=APP, agent=agente, session_service=InMemorySessionService())
    return _RUNNERS[nome]


def _executar(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("há um event loop ativo: use a versão *_async desta função")


def _runner_da_sessao(sessao_id: str, modelo: str | None = None) -> Runner:
    reg = _REGISTRO.get(sessao_id) or {}
    return criar_runner(modelo or reg.get("modelo"))


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


def _trace_add(state, ferramenta: str, argumentos: dict, resumo: str, numeros: list[dict], duracao_ms: int | None, etapa: str) -> None:
    trace = list(state.get("trace") or [])
    trace.append({"ordem": len(trace) + 1, "etapa": etapa, "ferramenta": ferramenta, "argumentos": argumentos, "resumo": resumo,
                  "numeros": numeros, "duracao_ms": duracao_ms, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  "llm": False})
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
        "finops": {"chamadas_llm": 0, "tokens_entrada": 0, "tokens_saida": 0, "latencias_ms": [], "custo_estimado": None},
        "ferramentas_usadas": [], "historico_contratacoes": list((simulacao or {}).get("historico_contratacoes") or []),
        "simulacao": {k: v for k, v in (simulacao or {}).items() if k in ("taxas", "liberacao")} or None,
        "recusou_oferta": False, "encaminhado": None, "cards_enviados": [],
    }
    _trace_add(estado, "fatura.historico", {"cliente_id": cliente_id[:8] + "…", "anomes": anomes, "n": 1},
               f"fatura do mês reconstruída pelo modo {atual['modo']} (só o mês; histórico só depois do consentimento)",
               numeros[:3], 0, "sessao")
    sess = await runner.session_service.create_session(app_name=APP, user_id=cliente_id, session_id=sid, state=estado)
    _REGISTRO[sess.id] = {"cliente_id": cliente_id, "modelo": runner.agent.model}
    return {
        "sessao_id": sess.id, "modo": "api",
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


# ------------------------------------------------------------------ /api/consentimento (sem LLM)
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
                      f"faltam {brl(m['falta'])}. Dá para juntar o que falta numa parcela que cabe no seu orçamento."),
            "botao": {"rotulo": "Conversar com o ia.i", "acao": "ver_opcoes"}}


def _fatura_de(state) -> dict:
    m = state.get("motor")
    if m:
        return m["fatura"]
    nums = state.get("numeros_validados") or []
    pega = lambda o: next((n["valor"] for n in nums if n["origem"] == o), 0)  # noqa: E731
    return {"valor": pega("fatura.historico:fatura.valor"), "vencimento_dia": pega("fatura.historico:fatura.vencimento_dia"),
            "minimo": pega("fatura.historico:fatura.minimo")}


async def _analise_deterministica(runner: Runner, sessao) -> dict:
    """Roda motor + ofertas pelo caminho determinístico (gatilho do fechamento), gravando estado e trace."""
    ctx = _ctx(sessao)
    usadas_antes = list(ctx.state.get("ferramentas_usadas") or [])
    t0 = time.perf_counter()
    n0 = len(ctx.state.get("numeros_validados") or [])
    try:
        tools.analisar_fatura(tool_context=ctx)
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
    return {"consentimento": bool(st.get("consentimento")), "registro": st.get("consentimento_registro"),
            "insight": _insight(st), "numeros_validados": list(st.get("numeros_validados") or []), "simulado": True}


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
        op = (o or {}).get("opcoes", [{}])[state.get("opcao_confirmada") or 0] if o else {}
        cards.append({"tipo": "confirmacao", "dados": {
            "resumo": (f"Você paga {brl(p['pagar_agora'])} agora pela conta; {brl(p['valor_financiado'])} vira "
                       + (f"{p['n_parcelas']} parcelas de {brl(p['parcela'])}" if not p.get("dias") else f"cobertura por {p['dias']} dias ({brl(p['parcela'])} no total)")
                       + f"; termina em {calendario.rotulo(p['termina_em'])}; até a próxima fatura cabem {brl(p['teto_cartao_mes'])} no cartão."),
            "plano": p, "opcao": op, "teto_cartao_mes": p["teto_cartao_mes"],
            "aviso": "Simulação: nada é contratado. O contrato real só existe depois da aprovação e da assinatura no app."}})
    if state.get("encaminhado") and ("encaminhar_humano" in chamadas or acao == "falar_com_pessoa"):
        cards.append({"tipo": "encaminhamento", "dados": {"motivo": state["encaminhado"]["motivo"], "status": "encaminhado",
                                                          "texto": "Encaminhado para uma pessoa do time de atendimento, com o contexto desta conversa. Nenhum produto foi oferecido."}})
    if acao == "nao_quero":
        cards.append({"tipo": "aviso", "dados": {"texto": "Registrado. A IA não volta ao assunto até a próxima fatura.", "contratado": False}})
    return cards


def _mensagens_do_agente(texto: str) -> list[dict]:
    blocos = [b.strip() for b in texto.replace("\r", "").split("\n\n") if b.strip()]
    if len(blocos) > 3:
        blocos = blocos[:2] + [" ".join(blocos[2:])]
    return [{"papel": "agente", "texto": b} for b in blocos]


async def conversar_async(sessao_id: str, cliente_id: str, anomes: int, texto_ou_acao: str, valor: int | None = None,
                          modelo: str | None = None) -> dict:
    """Uma rodada da conversa. Devolve o formato de POST /api/mensagem (+ finops do turno)."""
    runner = criar_runner(modelo or (_REGISTRO.get(sessao_id) or {}).get("modelo"))
    sessao = await _obter_sessao(runner, sessao_id, cliente_id)
    if sessao is None:
        r = await criar_sessao_async(cliente_id, anomes, sessao_id=sessao_id, modelo=runner.agent.model)
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

    st0 = sessao.state
    n_trace = len(st0.get("trace") or [])
    fin0 = dict(st0.get("finops") or {})
    await _aplicar_delta(runner, sessao, {"guardiao": {"removidos": [], "termos_bloqueados": [], "substituicoes": 0}})
    t0 = time.perf_counter()
    textos: list[str] = []
    try:
        async for ev in runner.run_async(user_id=sessao.user_id, session_id=sessao.id,
                                         new_message=types.Content(role="user", parts=[types.Part(text=texto)])):
            if ev.author == runner.agent.name and ev.content and ev.content.parts and not getattr(ev, "partial", False):
                for p in ev.content.parts:
                    if p.text and not p.function_call and not p.function_response and not getattr(p, "thought", False):
                        textos.append(p.text)
    except Exception as e:  # modelo indisponível, credencial, quota: a demo cai no caminho sem LLM
        callbacks._log("erro_modelo", sessao=sessao_id, erro=type(e).__name__, detalhe=str(e)[:300])
        return {**_erro(f"modelo: {type(e).__name__}: {str(e)[:200]}", MENSAGEM_ERRO_MODELO),
                "mensagens": [{"papel": "agente", "texto": MENSAGEM_ERRO_MODELO}], "cards": [], "sugestoes": [CHIP_PESSOA],
                "numeros_validados": list(st0.get("numeros_validados") or []), "guardiao": st0.get("guardiao")}
    duracao = int((time.perf_counter() - t0) * 1000)

    sessao = await _obter_sessao(runner, sessao_id, cliente_id)
    st = sessao.state
    texto_final = "\n\n".join(t.strip() for t in textos if t.strip()).strip()
    texto_final, rel = callbacks.guardiao_texto(texto_final, st.get("numeros_validados"))   # segunda passada: cobre o texto juntado
    novos = [t for t in (st.get("trace") or [])[n_trace:]]
    chamadas = [t["ferramenta"] for t in novos if not t.get("bloqueada") and not t.get("llm")]
    bloqueadas = any(t.get("bloqueada") for t in novos)
    delta = {}
    if acao == "nao_quero":
        delta["recusou_oferta"] = True
    cards = _cards(st, acao, chamadas, bloqueadas)
    delta["cards_enviados"] = sorted(set(st.get("cards_enviados") or []) | {c["tipo"] for c in cards})
    guard = dict(st.get("guardiao") or {})
    if rel["removidos"] or rel["substituicoes"]:
        guard["removidos"] = list(guard.get("removidos") or []) + rel["removidos"]
        guard["termos_bloqueados"] = list(guard.get("termos_bloqueados") or []) + rel["termos_bloqueados"]
        guard["substituicoes"] = int(guard.get("substituicoes") or 0) + rel["substituicoes"]
        delta["guardiao"] = guard
    await _aplicar_delta(runner, sessao, delta)
    st = (await _obter_sessao(runner, sessao_id, cliente_id)).state
    fin = st.get("finops") or {}
    lat = list(fin.get("latencias_ms") or [])[len(fin0.get("latencias_ms") or []):]
    return {
        "sessao_id": sessao_id,
        "mensagens": _mensagens_do_agente(texto_final or callbacks.RESPOSTA_VAZIA),
        "cards": cards,
        "numeros_validados": list(st.get("numeros_validados") or []),
        "guardiao": {"removidos": [r["frase"] for r in guard.get("removidos") or []], "termos_bloqueados": list(guard.get("termos_bloqueados") or []),
                     "substituicoes": int(guard.get("substituicoes") or 0)},
        "sugestoes": _sugestoes(st, acao),
        "finops": {"chamadas_llm": int(fin.get("chamadas_llm", 0)) - int(fin0.get("chamadas_llm", 0)),
                   "tokens_entrada": int(fin.get("tokens_entrada", 0)) - int(fin0.get("tokens_entrada", 0)),
                   "tokens_saida": int(fin.get("tokens_saida", 0)) - int(fin0.get("tokens_saida", 0)),
                   "latencias_ms": lat, "duracao_turno_ms": duracao, "ferramentas": chamadas},
        "simulado": True,
    }


def conversar(sessao_id: str, cliente_id: str, anomes: int, texto_ou_acao: str, valor: int | None = None, modelo: str | None = None) -> dict:
    return _executar(conversar_async(sessao_id, cliente_id, anomes, texto_ou_acao, valor, modelo))


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
    _trace_add(ctx.state, "acompanhar.ciclo", {"anomes": seguinte, "fonte_mes": c["fonte_mes"]}, c["frase"],
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


def finops_de(state) -> dict:
    f = state.get("finops") or {}
    p50, p95 = callbacks.p50_p95(list(f.get("latencias_ms") or []))
    return {"chamadas_llm": int(f.get("chamadas_llm", 0)), "tokens_entrada": int(f.get("tokens_entrada", 0)),
            "tokens_saida": int(f.get("tokens_saida", 0)), "latencia_p50_ms": p50, "latencia_p95_ms": p95,
            "custo_estimado": None, "modelo": agent_mod.modelo_configurado(),
            "nota": "custo_estimado fica null: não há preço por token com fonte em config/taxas.yaml (preco_modelo)"}


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
    j["consentimento"] = {"concedido": bool(st.get("consentimento")), "registro": st.get("consentimento_registro")}
    return j


def painel(sessao_id: str, modelo: str | None = None) -> dict:
    return _executar(painel_async(sessao_id, modelo))


def saude() -> dict:
    try:
        nome = tools.fonte().nome
    except Exception as e:  # sem CSV/BigQuery
        return {"ok": False, "dados": None, "modelo": agent_mod.modelo_configurado(), "erro": str(e)[:200]}
    return {"ok": True, "dados": nome, "modelo": agent_mod.modelo_configurado(), "app": APP}


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
