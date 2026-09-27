"""Conversa da API: modo de reserva sem LLM e ponte para cabe_no_bolso.runtime.conversar (fase 2, agente ADK).

Tudo que é número vem de cabe_core (motor, ofertas, acompanhar, painel, travas) e entra em state['numeros_validados']
com origem. Os textos daqui são montados em código a partir desses números e, mesmo assim, passam pelo guardião
(server.guardiao) antes de sair. A instrução do modelo nunca decide elegibilidade: isso é policy (cabe_core.travas).

Contrato esperado de cabe_no_bolso.runtime (quando existir):
    conversar(estado: dict, *, texto: str | None, acao: str | None, valor: int | None, fonte, taxas) -> dict
    -> {"mensagens": [{papel, texto}], "cards": [{tipo, dados}], "sugestoes": [...]?, "numeros_validados": [...]?,
        "guardiao": {removidos, termos_bloqueados}?}
    Pode ser síncrona ou assíncrona. Pode gravar em estado['trace'], estado['numeros_validados'] (via
    server.sessoes.adicionar_numeros) e estado['finops'] (chamadas_llm, tokens_entrada, tokens_saida, latencias_ms).
    Se o módulo não existir, não tiver `conversar`, devolver algo sem `mensagens` ou levantar exceção, a resposta é
    montada aqui, sem LLM, e marcada "modo": "sem_llm".

Dois modos com LLM (MODO_CONVERSA=gi|tools|auto; docs/notas-prompt-gi-2026-09-27.md):
- tools: o agente ADK com ferramentas (fase 2); o servidor passa a ação/texto como hoje.
- gi: "um agente, dois modos, um validador". O servidor mapeia a ação do cliente para (modo, gatilho) do prompt da Gi
  (GATILHOS), passa `gatilho=`/`modo=` ao runtime quando a assinatura aceita, resolve em código as ações
  determinísticas (confirmar, falar_com_pessoa, nao_quero, pagar_total: ACOES_DETERMINISTICAS_GI) e trata como
  opcionais os campos novos da resposta: acao, oferta_id, numeros_citados, validador, checagens, regeneracoes,
  mensagem_segura. Ganchos opcionais do runtime, todos sondados com getattr: modo_conversa() -> 'gi'|'tools';
  insight[_async](sessao_id, gatilho=...) -> {texto, botao_primario, botao_secundario, numeros_citados, validador};
  atualizar_estado[_async](sessao_id, delta) para espelhar no state do agente o que o servidor decidiu em código.
Nos dois modos, cada turno passa por checagens_borda (JSON/formato, números, oferta, consentimento, tamanho, termos
proibidos) e é registrado em estado['turnos'] com o veredito do validador, para o painel da banca.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import os
import re
import time

from starlette.concurrency import run_in_threadpool

from cabe_core import acompanhar, calendario, capacidade, fatura as fatura_mod, finops as finops_core, ofertas as ofertas_mod, painel as painel_mod, travas
from cabe_core.dinheiro import brl
from cabe_no_bolso import policy

from . import finops as finops_srv, guardiao
from .sessoes import adicionar_numeros, registrar_historico, registrar_trace, registrar_turno

log = logging.getLogger("cabe_no_bolso.server")

VERSAO_CONSENTIMENTO = "consentimento-v1-2026-09-27"
ESCOPO_CONSENTIMENTO = "últimos 90 dias de conta e cartão; revogável a qualquer momento"
ESCOPO_RECUSADO = "recusado; o pedido volta no máximo uma vez por mês, nunca no meio do pagamento"
ERRO_DADOS = "Não consegui ler seu extrato agora. Posso tentar de novo ou te passar para uma pessoa."
ANOMES_FIM_BASE = int(os.environ.get("ANOMES_FIM_BASE", "202512"))  # último mês da base: comparativo "2025 real"

ACOES = ("ver_opcoes", "pagar_total", "pagar_minimo", "pagar_outro_valor", "consigo_pagar", "por_que_alta", "confirmar",
         "falar_com_pessoa", "nao_quero", "contar_pix", "nao_contar_pix", "confirmar_entrada_regular")
ALIAS_ACAO = {"contar_pix": "confirmar_entrada_regular"}   # nome antigo da demo; o canônico é o da ação do prompt da Gi

# Modo da Gi: ação do cliente -> (modo, gatilho) do prompt (docs/prompt-gi-2026-09-27.md, <quando_voce_atua>).
# None = texto livre. As ações fora deste mapa são resolvidas em código, sem chamar o modelo.
GATILHOS = {
    "ver_opcoes": ("conversa", "fechamento"),
    "pagar_outro_valor": ("conversa", "pagar_outro_valor"),
    "pagar_minimo": ("conversa", "pagar_outro_valor"),        # valor = mínimo
    "consigo_pagar": ("conversa", "pergunta_cliente"),
    "por_que_alta": ("conversa", "pergunta_cliente"),
    "confirmar_entrada_regular": ("conversa", "fechamento"),  # recalcula com contar_pix=True e segue
    "nao_contar_pix": ("conversa", "fechamento"),
    None: ("conversa", "pergunta_cliente"),
}
ACOES_DETERMINISTICAS_GI = ("confirmar", "falar_com_pessoa", "nao_quero", "pagar_total")
MODOS_LLM = ("gi", "tools")
ACOES_AGENTE = ("nenhuma", "mostrar_formas_de_pagar", "mostrar_oferta", "abrir_resumo_contrato", "registrar_permissao_ampliacao",
                "mudar_vencimento", "revogar_consentimento", "transferir_humano", "devolver_ao_iai")
ACOES_COM_OFERTA = ("mostrar_oferta", "abrir_resumo_contrato", "registrar_permissao_ampliacao")
# Termos que nunca chegam ao cliente (lista do prompt da Gi + 'sujeito a' + produtos fora do plano de docs/05).
TERMOS_PROIBIDOS = ("score", "escorregão", "escorregao", "rolando a fatura", "pedalada", "da mais barata para a mais cara",
                    "análise de risco", "analise de risco", "negativa de crédito", "negativa de credito", "limite recusado",
                    "grupo do cliente", "sujeito a", "sujeita a", "sujeitos a", "sujeitas a") + policy.TERMOS_FORA_DO_PLANO
MAX_INSIGHT = 160
MAX_MENSAGENS_CONVERSA = 3
FRASE_VALIDADOR = "Nenhuma mensagem chega ao cliente sem passar pelo validador"
CHAVES_FINOPS = ("chamadas_llm", "chamadas_validador", "tokens_entrada", "tokens_saida", "tokens_entrada_validador", "tokens_saida_validador")

CHIP_HUMANO = {"rotulo": "Falar com uma pessoa", "acao": "falar_com_pessoa", "secundario": True}
CHIP_VER = {"rotulo": "Ver opções", "acao": "ver_opcoes"}
CHIP_AVANCAR = {"rotulo": "Avançar um mês (demo)", "acao": "avancar_mes"}
CHIPS_PIX = [{"rotulo": "É renda", "acao": "confirmar_entrada_regular"}, {"rotulo": "Não é renda", "acao": "nao_contar_pix", "secundario": True}]
AVISO_CONTRATO = "Nada é contratado agora. Depende de aprovação do serviço de crédito e da sua confirmação no app."
TEXTO_HUMANO = "Vou te passar para uma pessoa do time, com o que a gente já viu aqui. Você não precisa repetir nada."
TEXTO_ENCAMINHADO = "Encaminhado para uma pessoa do time de atendimento, com o contexto desta conversa. Nenhum produto foi oferecido."
TEXTO_SEM_PLANO = ("Por aqui eu não consigo montar um plano que caiba. Vou te passar para uma pessoa do time de renegociação, "
                   "com tudo o que a gente já viu.")
TEXTO_SEM_CONSENTIMENTO = ("Para olhar seu mês eu preciso da sua permissão. Ela fica no cartão, no topo da fatura. "
                           "Sem ela, mostro só as formas de pagar.")


class SemConsentimento(Exception):
    """Ferramenta de dados chamada sem state['consentimento'] (equivale ao before_tool_callback do agente)."""


# ----------------------------------------------------------------------------- utilidades
def _mes(anomes: int) -> str:
    return calendario.rotulo(anomes)


def numeros_config(taxas: dict) -> list[dict]:
    """Números que vêm de config/taxas.yaml e podem aparecer nos textos (regra 2: taxa e parâmetro só do YAML)."""
    janela = int(taxas.get("janela_meses", 3))
    return [
        {"valor": janela * 30, "origem": "config.taxas:janela_meses*30"},
        {"valor": int(taxas.get("regras_plano", {}).get("faturas_inteiras_para_encerrar", 3)), "origem": "config.taxas:regras_plano.faturas_inteiras_para_encerrar"},
        {"valor": 12, "origem": "config.taxas:parcelamento.vezes_por_12_meses.janela"},
        {"valor": int(taxas.get("cobertura_curta", {}).get("teto_dias", 25)), "origem": "config.taxas:cobertura_curta.teto_dias"},
        {"valor": int(round(float(taxas["rotativo"].get("teto_encargos_pct", 1.0)) * 100)), "origem": "config.taxas:rotativo.teto_encargos_pct*100"},
        {"valor": float(taxas["rotativo"]["taxa_mes"]), "origem": "config.taxas:rotativo.taxa_mes"},
    ]


def _resposta(estado: dict, mensagens: list[dict], cards: list[dict], sugestoes: list[dict] | None, acao: str = "nenhuma") -> dict:
    r = {
        "mensagens": mensagens,
        "cards": cards,
        "numeros_validados": [dict(n) for n in estado["numeros_validados"]],
        "guardiao": {"removidos": [], "termos_bloqueados": []},
        "sugestoes": sugestoes if sugestoes is not None else [],
        "acao": acao,
        "oferta_id": None,
        "numeros_citados": [],
        "validador": None,
        "modo": "sem_llm",
        "modo_conversa": "sem_llm",
        "simulado": True,
    }
    return guardiao.conferir_resposta(r, estado["numeros_validados"])


def _msg(texto: str) -> dict:
    return {"papel": "agente", "texto": texto}


def normalizar_acao(acao: str | None) -> str | None:
    return ALIAS_ACAO.get(acao, acao) if acao else None


def insight_seguro(estado: dict) -> dict:
    """Texto fixo do card (Notas da Gi): sem consentimento, ou quando o modelo falha, só a fatura e as formas de pagar."""
    f = estado["fatura"]
    return {"estado": "sem_adesao" if estado.get("consentimento") is not True else "sem_credito",
            "texto": f"Sua fatura fechou em {brl(f['valor'])} e vence dia {f['vencimento_dia']}. Veja as formas de pagar.",
            "botao": {"rotulo": "Ver formas de pagar", "acao": "ver_formas_de_pagar"}, "botao_secundario": None,
            "gerado_por": "codigo", "numeros_citados": [brl(f["valor"]), f"dia {f['vencimento_dia']}"], "validador": None}


def mensagem_segura(estado: dict) -> list[dict]:
    """Mensagem segura do chat (Notas da Gi): a mesma frase do insight mais a oferta de falar com alguém da equipe."""
    return [_msg(insight_seguro(estado)["texto"]), _msg("Se quiser, alguém da equipe pode olhar com você o melhor caminho para este mês.")]


def _pergunta_pix_pendente(estado: dict) -> bool:
    m = estado.get("motor") or {}
    fl = m.get("flags") or {}
    return bool(fl.get("pix_regular")) and estado.get("contar_pix") is None and not estado.get("plano") and not estado.get("encerrado")


def entrada_regular_a_confirmar(estado: dict) -> dict | None:
    """O PIX que entra todo mês (flag do motor), ainda sem resposta do cliente. Nunca somado por conta própria."""
    if not _pergunta_pix_pendente(estado):
        return None
    fl = estado["motor"]["flags"]
    return {"tipo": "pix", "valor": fl["pix_mensal_mediana"], "valor_texto": brl(fl["pix_mensal_mediana"]),
            "pergunta": "Ele é renda sua?", "origem": "capacidade.motor:flags.pix_mensal_mediana"}


def _custo_escolha(estado: dict, taxas: dict, valor_pago: int) -> dict:
    """O que acontece se o cliente pagar `valor_pago`: não pago e juros do próximo mês (funções de cabe_core)."""
    f = estado["fatura"]
    rot = taxas["rotativo"]
    t0 = time.perf_counter()
    nao_pago = fatura_mod.nao_pago(f["valor"], valor_pago)
    custo = travas.custo_rotativo(nao_pago, float(rot["taxa_mes"]), 1, float(rot.get("teto_encargos_pct", 1.0)))
    nums = [{"valor": int(valor_pago), "origem": "fatura.nao_pago:escolha.paga_agora"},
            {"valor": nao_pago, "origem": "fatura.nao_pago:escolha.nao_pago"},
            {"valor": custo, "origem": "travas.custo_rotativo:escolha.custo_1_mes"}]
    adicionar_numeros(estado, nums)
    registrar_trace(estado, "travas.custo_rotativo", {"nao_pago": nao_pago, "meses": 1, "taxa_mes": rot["taxa_mes"]},
                    f"se pagar {brl(valor_pago)}: ficam {brl(nao_pago)} para trás; juros de um mês {brl(custo)}", nums,
                    (time.perf_counter() - t0) * 1000, etapa="pagar")
    return {"paga_agora": int(valor_pago), "nao_pago": nao_pago, "custo_1_mes": custo}


# ----------------------------------------------------------------------------- sessão e consentimento
def abrir_sessao(estado: dict, fonte, taxas: dict) -> dict:
    """POST /api/sessao: fatura fechada do mês (sistema de cartões). O histórico de 90 dias só depois do consentimento."""
    cid, anomes = estado["cliente_id"], estado["anomes"]
    t0 = time.perf_counter()
    atual = next((f for f in fonte.faturas(cid, anomes, n=1) if f["anomes"] == anomes), None)
    if atual is None:
        raise capacidade.DadosIndisponiveis(f"sem fatura em {anomes}")
    rot = taxas["rotativo"]
    valor = int(atual["fatura"])
    minimo = fatura_mod.minimo(valor, float(rot.get("minimo_pct_fatura", 0.15)))
    f = {"valor": valor, "vencimento_dia": int(atual["dia_vencimento"]), "minimo": minimo, "modo_na_base": atual["modo"],
         "pago_na_base": int(atual["pago"]), "juros_na_base": int(atual["juros"])}
    estado["fatura"] = f
    nums = [{"valor": f["valor"], "origem": "capacidade.motor:fatura.valor"},
            {"valor": f["vencimento_dia"], "origem": "capacidade.motor:fatura.vencimento_dia"},
            {"valor": f["minimo"], "origem": "capacidade.motor:fatura.minimo"},
            {"valor": f["pago_na_base"], "origem": "capacidade.motor:fatura.pago_na_base"}]
    adicionar_numeros(estado, nums)
    adicionar_numeros(estado, numeros_config(taxas))
    registrar_trace(estado, "fatura.historico", {"cliente_id": cid, "anomes": anomes, "n": 1},
                    f"fatura do mês reconstruída pelo modo {atual['modo']} (sistema de cartões); histórico só após consentimento",
                    nums, (time.perf_counter() - t0) * 1000, etapa="sessao")
    estado["insight"] = insight_seguro(estado)   # sem consentimento o card só mostra a fatura e as formas de pagar
    return {
        "sessao_id": estado["sessao_id"],
        "modo": estado["modo"],
        "modo_conversa": estado.get("modo_conversa"),
        "cliente": {"apelido": estado["apelido"], "perfil": estado["perfil_texto"], "grupo_rotulo": estado.get("grupo_rotulo_config")},
        "anomes": anomes,
        "mes_rotulo": _mes(anomes),
        "data_simulada": f"{max(f['vencimento_dia'] - 7, 1):02d}/{anomes % 100:02d}/{anomes // 100}",
        "fatura": {"valor": f["valor"], "vencimento_dia": f["vencimento_dia"], "minimo": f["minimo"],
                   "opcoes_pagamento": [
                       {"rotulo": "Total", "valor": f["valor"], "acao": "pagar_total"},
                       {"rotulo": "Mínimo", "valor": f["minimo"], "acao": "pagar_minimo"},
                       {"rotulo": "Outro valor", "valor": None, "acao": "pagar_outro_valor", "valor_gravado": f["pago_na_base"]}]},
        "consentimento": False,
        "insight": dict(estado["insight"]),
        "numeros_validados": [dict(n) for n in estado["numeros_validados"]],
        "simulado": True,
    }


def registrar_consentimento(estado: dict, fonte, taxas: dict, concedido: bool, data: str) -> dict:
    registro = {"data": data, "versao_texto": VERSAO_CONSENTIMENTO, "escopo": ESCOPO_CONSENTIMENTO if concedido else ESCOPO_RECUSADO}
    estado["consentimento"] = bool(concedido)
    estado["registro_consentimento"] = registro
    registrar_trace(estado, "registrar_consentimento", {"concedido": bool(concedido), "escopo": "90 dias", "versao": VERSAO_CONSENTIMENTO},
                    "consentimento registrado; ferramentas de dados liberadas (before_tool_callback)" if concedido
                    else "consentimento recusado; nenhuma ferramenta de dados roda", [], etapa="consentimento")
    insight = None
    if concedido:
        garantir_analise(estado, fonte, taxas)   # com adesão o motor roda no gatilho (spec)
        insight = {**_insight(estado), "gerado_por": "codigo", "validador": None}
        estado["insight"] = insight
        registrar_turno(estado, acao="consentimento", modo="insight", gatilho="fechamento", llm=False,
                        modo_conversa=estado.get("modo_conversa") or "sem_llm",   # o turno é da sessão (gi/tools); llm=False já diz que foi em código
                        checagens=checagens_insight(insight, estado), validador=validador_ausente("sem_llm"), regeneracoes=0,
                        mensagem_segura=False, chamadas_llm=0, tokens_entrada=0, tokens_saida=0, latencia_ms=None,
                        acao_agente=None, oferta_id=None, numeros_citados=[])
    return {"consentimento": estado["consentimento"], "registro": registro, "insight": insight,
            "turno": (estado["turnos"][-1] if concedido and estado.get("turnos") else None),
            "numeros_validados": [dict(n) for n in estado["numeros_validados"]], "simulado": True}


# ----------------------------------------------------------------------------- análise (motor + ofertas)
def garantir_analise(estado: dict, fonte, taxas: dict, forcar: bool = False) -> tuple[dict, dict]:
    """Roda capacidade.motor e ofertas.montar uma vez por sessão (ou de novo quando contar_pix muda). Exige consentimento."""
    if estado.get("consentimento") is not True:
        registrar_trace(estado, "before_tool_callback", {"ferramenta": "capacidade.motor"}, "bloqueado: sem consentimento, nenhum dado lido", [])
        raise SemConsentimento()
    if estado.get("motor") and estado.get("ofertas") and not forcar:
        return estado["motor"], estado["ofertas"]
    cid, anomes = estado["cliente_id"], estado["anomes"]
    contar_pix = bool(estado.get("contar_pix"))
    etapa = "consentimento" if not forcar else "analise"

    t0 = time.perf_counter()
    m = capacidade.motor(fonte, cid, anomes, taxas, contar_pix=contar_pix)
    dt = (time.perf_counter() - t0) * 1000
    adicionar_numeros(estado, m["numeros"])
    registrar_trace(estado, "capacidade.motor", {"cliente_id": cid, "anomes": anomes, "janela": m["janela"]["meses_fechados"], "contar_pix": contar_pix},
                    m["frase"], [{"valor": m[k], "origem": m["origem"][k]} for k in ("renda_recorrente", "fixos", "essenciais", "folga", "falta")
                                 if k in m["origem"]] + ([{"valor": m["dias_ate_recebimento"], "origem": m["origem"]["dias_ate_recebimento"]}]
                                                         if m.get("dias_ate_recebimento") is not None else []), dt, etapa=etapa)
    g = m["grupo"]
    registrar_trace(estado, "grupo.classificar", {"meses_considerados": g["meses_considerados"], "modo_atual": g["modo_atual"]},
                    f"grupo {g['grupo']} (flag calculada em código, fora do prompt)",
                    [{"valor": g["roladas_com_atual"], "origem": "capacidade.motor:grupo.roladas_com_atual"}], 0.0, etapa=etapa)
    fl = m["flags"]
    registrar_trace(estado, "anomalia.renda", {"metodo": taxas.get("anomalia", {}).get("metodo", "media_movel_lag")},
                    ("renda irregular" if fl["renda_irregular"] else "renda regular")
                    + f"; {len(fl['entradas_esporadicas'])} entrada(s) esporádica(s) sinalizada(s), fora da renda",
                    [{"valor": e["mediana_mensal"], "origem": f"capacidade.motor:flags.entradas_esporadicas[{i}].mediana_mensal"}
                     for i, e in enumerate(fl["entradas_esporadicas"])], 0.0, etapa=etapa)
    registrar_trace(estado, "anomalia.gastos", {"anomes": anomes}, f"{len(fl['gastos_atipicos'])} categoria(s) fora do padrão da janela",
                    [{"valor": x["valor"], "origem": f"capacidade.motor:flags.gastos_atipicos[{i}].valor"} for i, x in enumerate(fl["gastos_atipicos"])],
                    0.0, etapa=etapa)

    lib = policy.liberacao_para(cid, taxas)
    t0 = time.perf_counter()
    o = ofertas_mod.montar(m, m["grupo"], taxas, lib, estado.get("historico_contratacoes") or None)
    dt = (time.perf_counter() - t0) * 1000
    adicionar_numeros(estado, o["numeros"])
    registrar_trace(estado, "ofertas.montar", {"caminho": o["caminho"], "liberacao": [p for p, ok in lib.items() if ok]}, o["motivo"],
                    [{"valor": op["custo_total"], "origem": f"ofertas.montar:opcoes[{i}].custo_total"} for i, op in enumerate(o["opcoes"])]
                    + [{"valor": o["continuar_no_rotativo"]["custo_1_mes"], "origem": "ofertas.montar:continuar_no_rotativo.custo_1_mes"},
                       {"valor": o["teto_cartao_mes"], "origem": "ofertas.montar:teto_cartao_mes"}], dt, etapa="analise")
    registrar_trace(estado, "policy.travas", {"regras": ["parcela<=folga", "custo<rotativo no mesmo horizonte", "liberado", "1x/12m",
                                                        "conta voltou ao positivo", "sem reincidência", "grupo"]},
                    "; ".join([f"{op['produto']}: ok" for op in o["opcoes"]]
                              + [f"{op['produto']}: {', '.join(op['bloqueios'])}" for op in o["descartadas"]]) or "sem opções", [], 0.0, etapa="analise")
    estado["motor"], estado["ofertas"] = m, o
    return m, o


def _recomendada(estado: dict) -> dict | None:
    o = estado.get("ofertas") or {}
    return o["opcoes"][o["recomendada"]] if o.get("recomendada") is not None else None


def _insight(estado: dict) -> dict:
    m, o = estado["motor"], estado["ofertas"]
    f = m["fatura"]
    fat, venc = brl(f["valor"]), f["vencimento_dia"]
    rec = _recomendada(estado)
    if m["cabe"]:
        return {"estado": "cabe", "texto": f"Sua fatura de {fat} cabe no seu saldo previsto para o dia {venc}.", "botao": None}
    if o["caminho"] == "cobertura_curta" and rec:
        return {"estado": "falta_pontual", "botao": {"rotulo": "Ver opções no ia.i", "acao": "ver_opcoes"},
                "texto": f"Sua fatura fechou em {fat}. Até o dia {venc}, a previsão é ter {brl(m['folga'])}. Temos opções para pagar tudo."}
    if o["caminho"] == "parcelamento" and rec:
        # até 160 caracteres (modo insight do prompt da Gi): só texto, sem título
        return {"estado": "falta_que_se_repete", "botao": {"rotulo": "Conversar com o ia.i", "acao": "ver_opcoes"},
                "texto": (f"Sua fatura fechou em {fat} e vence dia {venc}. Cabe pagar {brl(o['pagar_agora'])}; "
                          f"faltam {brl(m['falta'])}. Dá para juntar o que falta numa parcela que cabe.")}
    return {"estado": "sem_credito", "botao": {"rotulo": "Ver opções da fatura", "acao": "ver_opcoes"},
            "texto": f"Sua fatura fechou em {fat} e vence dia {venc}. Veja as formas de pagar."}


# ----------------------------------------------------------------------------- textos (tom de docs/06; cenários da spec)
def _textos(estado: dict) -> dict:
    nome, m, o = estado["apelido"], estado["motor"], estado["ofertas"]
    f = m["fatura"]
    fat, venc, folga, falta = brl(f["valor"]), f["vencimento_dia"], brl(m["folga"]), brl(m["falta"])
    dias, receb = m["dias_ate_recebimento"], m["dia_recebimento"]
    cont = o["continuar_no_rotativo"]
    rec = _recomendada(estado)
    t = {
        "abertura": f"Oi, {nome}. Sou o ia.i, uma inteligência artificial do banco. Sua fatura fechou em {fat} e vence dia {venc}.",
        "texto_livre": "Sou uma IA e por aqui só consigo ajudar com a fatura deste mês. Quer ver as opções ou falar com uma pessoa?",
        "fora_do_plano": ("Por aqui eu não cuido disso e não ofereço esse tipo de produto. Consigo ajudar com a fatura deste mês. "
                          "Quer ver as opções ou falar com uma pessoa?"),
        "taxa": ("As taxas que uso vêm do registro do banco e aparecem em cada opção do comparador, com a fonte. "
                 "Fora dele, não sei dizer. Quer ver as opções?"),
        "angustia": "Entendo. Vou te passar para uma pessoa do time agora, com o que a gente já viu aqui. Você não precisa repetir nada.",
        "nao_quero_padrao": f"Tudo bem. Não volto a esse assunto até a próxima fatura. Se mudar de ideia até o dia {venc}, é só me chamar.",
        "ja_recusou": f"Registrado: você prefere continuar como está. Se mudar de ideia até o dia {venc}, é só me chamar.",
        "encerrado": "Uma pessoa do time vai continuar daqui. Se quiser recomeçar a demo, toque em Reiniciar.",
        "sem_credito": "Estas são as formas de pagar. Quer que eu explique alguma, ou prefere falar com alguém da equipe?",
        "pix_pergunta": (f"Uma pergunta: vi um PIX de cerca de {brl(m['flags']['pix_mensal_mediana'])} entrando todo mês. "
                         "Ele é renda sua? Se for, refaço as contas com ele."),
        "pix_refeito": f"Refiz as contas contando o PIX: cabe pagar {brl(o['pagar_agora'])} no dia {venc} e faltam {falta}.",
        "pix_fora": "Certo, deixo o PIX de fora das contas.",
    }
    if m["cabe"]:
        t.update(consigo_pagar=f"Sim. Sua fatura de {fat} cabe no seu mês. Está tudo certo para pagar o total.",
                 proposta=f"Sua fatura fechou em {fat} e a previsão de saldo no vencimento cobre o total. Está tudo certo para pagar inteira.",
                 comparador="", confirmacao="", fecho="", intercepta="")
    elif o["caminho"] == "cobertura_curta" and rec:
        t.update(
            abertura=t["abertura"] + f" Até lá, a previsão é ter {folga} na conta. Seu próximo salário cai dia {receb}.",
            consigo_pagar=f"Pelo seu mês, cabe pagar {folga} no dia {venc}. Para a fatura inteira, {m['frase']}. Quer ver um jeito de pagar tudo?",
            proposta=(f"Uma opção: pagar a fatura inteira no dia {venc} usando o limite da conta por {dias} dias, até o salário. "
                      f"Custo estimado: {brl(rec['custo_total'])}. Se pagar só o mínimo, os juros do cartão ficam em "
                      f"{brl(cont['se_pagar_minimo']['custo_1_mes'])} este mês. Quer ver as duas lado a lado?"),
            comparador=(f"A cobertura pelo limite da conta está liberada para você e custa {brl(rec['custo_total'])} por {dias} dias, "
                        f"contra {brl(cont['custo_1_mes'])} se a diferença ficar nos juros do cartão. Se fizer sentido, confirmo com você antes de qualquer coisa."),
            confirmacao=(f"Fica assim: no dia {venc} a fatura de {fat} é paga inteira, {brl(o['pagar_agora'])} da sua conta e "
                         f"{brl(rec['valor_financiado'])} pelo limite. No dia {receb} o salário cobre o limite; custo {brl(rec['custo_total'])}."),
            fecho=f"Combinado. No dia {receb}, quando o salário entrar, o limite da conta é coberto na hora. Te aviso quando zerar.",
            intercepta="Antes de confirmar: tem um jeito de pagar a fatura inteira com uma cobertura que cabe no seu mês.",
        )
    elif o["caminho"] == "parcelamento" and rec:
        t.update(
            abertura=t["abertura"] + f" Dei uma olhada no seu mês: cabe pagar {brl(o['pagar_agora'])} e faltam {falta}.",
            consigo_pagar=f"Pelo seu mês, cabe pagar {folga} no dia {venc}. Para a fatura inteira, {m['frase']}. Quer ver um jeito de juntar o que falta numa parcela?",
            proposta=(f"Tenho uma ideia que pode aliviar: juntar esses {falta} num parcelamento só, com {rec['n_parcelas']} parcelas de "
                      f"{brl(rec['parcela'])}, que cabem no que sobra no seu mês. Assim seu limite do cartão volta inteiro. "
                      "Quer ver as opções lado a lado, com o custo total de cada uma?"),
            comparador=(f"A opção com a {rec['rotulo_cliente']} está liberada para você e é a de menor custo total: {brl(rec['custo_total'])}, "
                        f"contra até {brl(cont['custo_ate_teto'])} se o que falta ficar nos juros do cartão. "
                        "Se fizer sentido, te mostro o resumo antes de qualquer coisa, e só seguimos com a sua confirmação."),
            confirmacao=(f"Fica assim: {brl(o['pagar_agora'])} agora; {falta} em {rec['n_parcelas']} parcelas de {brl(rec['parcela'])}; "
                         f"termina em {_mes(rec['termina_em'])}; até a próxima fatura cabem {brl(o['teto_cartao_mes'])} no cartão."),
            fecho=("Combinado. Te mostro o resumo do contrato antes de qualquer coisa e só seguimos com a sua confirmação. "
                   "Te acompanho nas próximas três faturas."),
            intercepta="Antes de confirmar: tem um jeito de pagar a fatura inteira com uma parcela que cabe no seu mês.",
        )
    else:
        t.update(consigo_pagar=f"Pelo seu mês, cabe pagar {folga} no dia {venc}. Para a fatura inteira, {m['frase']}. Uma pessoa do time pode ajudar.",
                 proposta=t["sem_credito"], comparador="", confirmacao="", fecho="", intercepta="")

    comp = m["flags"]["composicao_fatura"]
    cats = comp.get("categorias") or []
    partes = [f"Nesta fatura entraram {brl(comp['total_compras'])} de compras de {_mes(comp['anomes_compras'])}."]
    if cats:
        partes.append(f"{cats[0]['categoria']} foi {cats[0]['pct']}% disso ({brl(cats[0]['valor'])})"
                      + (f" e {cats[1]['categoria']} {cats[1]['pct']}% ({brl(cats[1]['valor'])})." if len(cats) > 1 else "."))
    if comp.get("mediana_compras"):
        partes.append(f"Seu mês típico de cartão é {brl(comp['mediana_compras'])}.")
    pc = comp.get("parcelas_em_curso") or {}
    if pc.get("quantidade"):
        partes.append(f"Também entrou {pc['quantidade']} parcela em curso, {brl(pc['valor'])}." if pc["quantidade"] == 1
                      else f"Também entraram {pc['quantidade']} parcelas em curso, {brl(pc['valor'])}.")
    partes.append("Quer ver as opções para pagar?")
    t["por_que_alta"] = " ".join(partes)
    return t


def _chips_analise(estado: dict) -> list[dict]:
    o = estado["ofertas"]
    if estado.get("plano"):
        return [] if estado["plano"].get("encerrado") else [CHIP_AVANCAR, CHIP_HUMANO]
    if o["recomendada"] is not None and not estado.get("oferta_recusada"):
        return [{"rotulo": "Quero pagar tudo" if o["caminho"] == "cobertura_curta" else "Quero essa opção", "acao": "confirmar"},
                {"rotulo": "Prefiro continuar como está", "acao": "nao_quero", "secundario": True}, CHIP_HUMANO]
    if estado["motor"]["cabe"]:
        return [CHIP_HUMANO]
    return [CHIP_HUMANO, {"rotulo": "Agora não", "acao": "nao_quero", "secundario": True}]


def _card_diagnostico(estado: dict) -> dict:
    return {"tipo": "diagnostico", "dados": estado["motor"]}


def _card_encaminhamento(motivo: str, status: str | None = "encaminhado (simulado)") -> dict:
    return {"tipo": "encaminhamento", "dados": {"motivo": motivo, "texto": TEXTO_ENCAMINHADO, "contratado": False, "status": status}}


# ----------------------------------------------------------------------------- roteamento de texto livre (só no modo sem LLM)
def rotear_texto(texto: str) -> str:
    """Entrada é dado: o texto do cliente só escolhe uma das respostas fixas; nunca vira instrução nem é ecoado."""
    t = (texto or "").lower()
    if policy.termos_bloqueados(t):
        return "fora_do_plano"
    if any(k in t for k in ("desesper", "angust", "não aguento", "nao aguento", "sem saída", "sem saida", "não sei mais", "nao sei mais",
                            "ansios", "chorar", "vergonha", "me ajuda por favor")):
        return "angustia"
    if any(k in t for k in ("pessoa", "humano", "atendente", "alguém", "alguem", "gerente")):
        return "falar_com_pessoa"
    if "taxa" in t or "cet" in t.split():
        return "taxa"
    if any(k in t for k in ("por que", "porque", "por quê", "tão alta", "tao alta", "veio alta", "tanto assim")):
        return "por_que_alta"
    if any(k in t for k in ("consigo pagar", "cabe", "dá para pagar", "da para pagar", "consigo")):
        return "consigo_pagar"
    if any(k in t for k in ("parcel", "opç", "opco", "limite", "cheque", "juros")):
        return "ver_opcoes"
    if "mínimo" in t or "minimo" in t:
        return "pagar_minimo"
    if any(k in t for k in ("não quero", "nao quero", "deixa pra lá", "deixa pra la", "prefiro continuar", "continuar como está")):
        return "nao_quero"
    return "texto_livre"


# ----------------------------------------------------------------------------- resposta sem LLM
def responder(estado: dict, fonte, taxas: dict, *, acao: str | None, texto: str | None, valor: int | None) -> dict:
    """Monta a resposta de /api/mensagem sem modelo de linguagem, só com o núcleo e textos fixos por cenário.

    Cada turno passa pelas mesmas checagens em código dos modos com LLM e entra em estado['turnos'] (validador ausente)."""
    acao = normalizar_acao(acao)
    t0 = time.perf_counter()
    r = _responder(estado, fonte, taxas, acao=acao, texto=texto, valor=valor)
    modo, gatilho = GATILHOS.get(acao, (None, None)) if (acao in GATILHOS or acao is None) else (None, None)
    r["checagens"] = checagens_borda(r, estado, modo or "conversa", gatilho)
    r["gatilho"] = gatilho
    r["modo_conversa"] = estado.get("modo_conversa") or "sem_llm"
    registrar_historico(estado, "cliente", ROTULO_CLIENTE.get(acao, acao) if acao else "(texto do cliente)")
    for m in r.get("mensagens") or []:
        registrar_historico(estado, "agente", m.get("texto"))
    registrar_turno(estado, acao=acao or "texto", modo=modo or "codigo", gatilho=gatilho, llm=False,
                    modo_conversa=estado.get("modo_conversa") or "sem_llm",   # turno em código dentro de uma sessão gi/tools continua rotulado pela sessão
                    checagens=r["checagens"], validador=validador_ausente("sem_llm"), regeneracoes=0,
                    mensagem_segura=bool(r["checagens"].get("mensagem_segura")), chamadas_llm=0, tokens_entrada=0, tokens_saida=0,
                    latencia_ms=round((time.perf_counter() - t0) * 1000, 1), acao_agente=r.get("acao"), oferta_id=r.get("oferta_id"),
                    numeros_citados=list(r.get("numeros_citados") or []))
    r["turno"] = dict(estado["turnos"][-1])
    return r


ROTULO_CLIENTE = {"ver_opcoes": "Ver opções", "consigo_pagar": "Consigo pagar minha fatura?", "por_que_alta": "Por que minha fatura veio tão alta?",
                  "confirmar": "Quero essa opção", "falar_com_pessoa": "Quero falar com uma pessoa", "nao_quero": "Prefiro continuar como está",
                  "pagar_minimo": "Vou pagar o mínimo", "pagar_outro_valor": "Vou pagar outro valor", "pagar_total": "Vou pagar o total",
                  "confirmar_entrada_regular": "É renda", "nao_contar_pix": "Não é renda"}


def _responder(estado: dict, fonte, taxas: dict, *, acao: str | None, texto: str | None, valor: int | None) -> dict:
    f = estado["fatura"]
    venc = f["vencimento_dia"]

    if estado.get("encerrado"):
        return _resposta(estado, [_msg("Uma pessoa do time vai continuar daqui. Se quiser recomeçar a demo, toque em Reiniciar.")], [], [])

    if acao is None and texto:
        rota = rotear_texto(texto)
        if rota == "texto_livre":
            return _resposta(estado, [_msg("Sou uma IA e por aqui só consigo ajudar com a fatura deste mês. Quer ver as opções ou falar com uma pessoa?")],
                             [], [CHIP_VER, CHIP_HUMANO])
        if rota == "fora_do_plano":
            return _resposta(estado, [_msg("Por aqui eu não cuido disso e não ofereço esse tipo de produto. Consigo ajudar com a fatura deste mês. "
                                           "Quer ver as opções ou falar com uma pessoa?")], [], [CHIP_VER, CHIP_HUMANO])
        if rota == "taxa":
            return _resposta(estado, [_msg("As taxas que uso vêm do registro do banco e aparecem em cada opção do comparador, com a fonte. "
                                           "Fora dele, não sei dizer. Quer ver as opções?")], [], [CHIP_VER, CHIP_HUMANO])
        if rota == "angustia":
            estado["encerrado"] = True
            registrar_trace(estado, "policy.encaminhar_humano", {"motivo": "sinal de angústia"}, "encaminhado a uma pessoa; nenhum produto", [], etapa="analise")
            return _resposta(estado, [_msg("Entendo. Vou te passar para uma pessoa do time agora, com o que a gente já viu aqui. Você não precisa repetir nada.")],
                             [_card_encaminhamento("sinal de angústia na conversa")], [], acao="transferir_humano")
        acao = rota
        if acao == "pagar_minimo":
            valor = f["minimo"]

    if acao == "falar_com_pessoa":
        estado["encerrado"] = True
        registrar_trace(estado, "policy.encaminhar_humano", {"motivo": "pedido do cliente"}, "encaminhado a uma pessoa; nenhum produto", [], etapa="analise")
        return _resposta(estado, [_msg(TEXTO_HUMANO)], [_card_encaminhamento("pedido do cliente")], [], acao="transferir_humano")

    if acao == "pagar_total":
        estado["escolha"] = {"acao": acao, "valor": f["valor"]}
        return _resposta(estado, [], [{"tipo": "aviso", "dados": {"texto": f"Pagamento do total ({brl(f['valor'])}) registrado. Nenhum pagamento real foi feito.",
                                                                  "paga_agora": f["valor"], "contratado": False}}], [])

    if estado.get("consentimento") is not True:
        # sem adesão: nenhuma ferramenta de dados roda; só as formas de pagar (spec; golden set 5)
        registrar_trace(estado, "before_tool_callback", {"acao": acao or "texto"}, "bloqueado: sem consentimento, nenhum dado lido", [], etapa="sessao")
        if acao in ("pagar_minimo", "pagar_outro_valor"):
            v = f["minimo"] if acao == "pagar_minimo" else int(valor or 0)
            estado["escolha"] = {"acao": acao, "valor": v}
            return _resposta(estado, [], [{"tipo": "aviso", "dados": {"texto": f"Pagamento de {brl(v)} registrado. Nenhum pagamento real foi feito.",
                                                                      "paga_agora": v, "contratado": False}}], [])
        return _resposta(estado, [_msg(TEXTO_SEM_CONSENTIMENTO)], [],
                         [{"rotulo": "Voltar ao cartão", "acao": "ir_cartao"}, CHIP_HUMANO], acao="mostrar_formas_de_pagar")

    garantir_analise(estado, fonte, taxas)
    m, o = estado["motor"], estado["ofertas"]
    t = _textos(estado)
    rec = _recomendada(estado)
    diagnostico = _card_diagnostico(estado)

    if acao in ("pagar_minimo", "pagar_outro_valor"):
        v = f["minimo"] if acao == "pagar_minimo" else int(valor or 0)
        estado["escolha"] = {"acao": acao, "valor": v}
        if v >= f["valor"]:
            return _resposta(estado, [], [{"tipo": "aviso", "dados": {"texto": "Com esse valor a fatura fica paga inteira. Está tudo certo.",
                                                                      "paga_agora": v, "contratado": False}}], [])
        if rec and not estado.get("oferta_recusada") and not estado.get("plano"):
            return _resposta(estado, [], [{"tipo": "insight", "dados": {
                "estado": "antes_de_confirmar", "texto": t["intercepta"], "paga_agora": v,
                "botao_primario": {"rotulo": "Ver opção", "acao": "ver_opcoes"},
                "botao_secundario": {"rotulo": "Continuar com este valor", "acao": "nao_quero"}}}], [])
        c = _custo_escolha(estado, taxas, v)
        return _resposta(estado, [], [{"tipo": "aviso", "dados": {
            "texto": (f"Tudo bem. Só para você saber: com esse valor, ficam {brl(c['nao_pago'])} para trás e os juros do cartão do próximo mês "
                      f"ficam em {brl(c['custo_1_mes'])}. Se mudar de ideia até o dia {venc}, é só me chamar."),
            "paga_agora": v, "contratado": False}}], [])

    if acao == "nao_quero":
        estado["oferta_recusada"] = True
        registrar_trace(estado, "registrar_recusa", {"escolha": (estado.get("escolha") or {}).get("acao")},
                        "oferta recusada: a IA informa o custo uma vez e não volta ao assunto neste ciclo", [], etapa="analise")
        esc = estado.get("escolha")
        if esc and esc["valor"] < f["valor"]:
            c = _custo_escolha(estado, taxas, esc["valor"])
            texto_r = (f"Tudo bem. Só para você saber: com {'o mínimo' if esc['acao'] == 'pagar_minimo' else 'esse valor'}, os juros do cartão do "
                       f"próximo mês ficam em {brl(c['custo_1_mes'])}. Se mudar de ideia até o dia {venc}, é só me chamar.")
        else:
            texto_r = t["nao_quero_padrao"]
        return _resposta(estado, [_msg(texto_r)], [{"tipo": "aviso", "dados": {"texto": "Registrado. A IA não volta ao assunto até a próxima fatura.",
                                                                            "contratado": False}}], [])

    if acao in ("confirmar_entrada_regular", "nao_contar_pix"):
        if estado.get("plano"):
            return _resposta(estado, [_msg("O plano já está confirmado; as contas ficam como estão.")], [], _chips_analise(estado))
        estado["contar_pix"] = acao == "confirmar_entrada_regular"
        if acao == "confirmar_entrada_regular":
            garantir_analise(estado, fonte, taxas, forcar=True)
            t = _textos(estado)
            r = _resposta(estado, [_msg(t["pix_refeito"]), _msg(t["proposta"])],
                          [_card_diagnostico(estado), {"tipo": "comparador", "dados": estado["ofertas"]}], _chips_analise(estado),
                          acao="mostrar_oferta" if _recomendada(estado) else "mostrar_formas_de_pagar")
            r["oferta_id"] = id_oferta(estado["ofertas"], estado["ofertas"]["recomendada"])
            return r
        return _resposta(estado, [_msg(t["pix_fora"])], [], _chips_analise(estado))

    if acao == "confirmar":
        if estado.get("plano"):
            return _resposta(estado, [_msg("O plano já está confirmado. Vamos acompanhar as próximas faturas.")], [_card_confirmacao(estado, t)], _chips_analise(estado))
        if not rec or estado.get("oferta_recusada"):
            estado["encerrado"] = True
            registrar_trace(estado, "policy.encaminhar_humano", {"motivo": o["motivo"]}, "sem opção que caiba: encaminhado; nenhum produto", [], etapa="analise")
            return _resposta(estado, [_msg(TEXTO_SEM_PLANO)], [_card_encaminhamento("por aqui não há plano que caiba")], [], acao="transferir_humano")
        if rec.get("requer_confirmacao_humana"):
            estado["encerrado"] = True
            return _resposta(estado, [_msg(TEXTO_HUMANO)], [_card_encaminhamento("a opção exige confirmação com uma pessoa")], [], acao="transferir_humano")
        registrar_plano(estado, None)
        r = _resposta(estado, [_msg(t["fecho"])], [_card_confirmacao(estado, t)], [CHIP_AVANCAR, CHIP_HUMANO], acao="abrir_resumo_contrato")
        r["oferta_id"] = id_oferta(o, o["recomendada"])
        return r

    if acao == "consigo_pagar":
        return _resposta(estado, [_msg(t["consigo_pagar"])], [diagnostico], [CHIP_VER, CHIP_HUMANO] if not m["cabe"] else [CHIP_HUMANO])

    if acao == "por_que_alta":
        return _resposta(estado, [_msg(t["por_que_alta"])],
                         [{"tipo": "diagnostico", "dados": {"so_categorias": True, "fatura": m["fatura"], "flags": m["flags"], "anomes": m["anomes"]}}],
                         [CHIP_VER, CHIP_HUMANO])

    # ver_opcoes (padrão)
    if rec:
        msgs = [_msg(t["abertura"] if not estado.get("oferta_recusada") else t["ja_recusou"]), _msg(t["proposta"]), _msg(t["comparador"])]
        cards = [diagnostico, {"tipo": "comparador", "dados": o}]
        chips = _chips_analise(estado)
        if _pergunta_pix_pendente(estado):
            # a regra nossa das diretrizes: pergunta uma vez se o PIX é renda antes de seguir; nunca soma por conta própria
            msgs = msgs[:2] + [_msg(t["pix_pergunta"])]
            chips = CHIPS_PIX + chips
        r = _resposta(estado, msgs, cards, chips, acao="mostrar_oferta")
        r["oferta_id"] = id_oferta(o, o["recomendada"])
        return r
    if m["cabe"]:
        return _resposta(estado, [_msg(t["abertura"]), _msg(t["proposta"])], [diagnostico], [CHIP_HUMANO])
    cards = [diagnostico]
    if o.get("encaminhar_humano") and not o.get("opcoes"):
        registrar_trace(estado, "policy.encaminhar_humano", {"motivo": o["motivo"]}, "sem crédito automático: opções da fatura e uma pessoa", [], etapa="analise")
        cards.append({"tipo": "encaminhamento", "dados": {"motivo": "por aqui não há plano que caiba", "contratado": False,
                                                          "texto": "Você pode falar com uma pessoa do time a qualquer momento; ela recebe o contexto desta conversa."}})
    cards.append(card_formas_de_pagar(estado))
    return _resposta(estado, [_msg(t["abertura"]), _msg(t["proposta"])], cards, [CHIP_HUMANO, {"rotulo": "Agora não", "acao": "nao_quero", "secundario": True}],
                     acao="mostrar_formas_de_pagar")


# Mesmo esquema de ids de cabe_no_bolso.contexto (o runtime no modo gi): prefixo por tipo + contador por tipo.
TIPO_OFERTA = {"cheque_especial": "cobertura_cheque_especial", "consignado_clt": "credito_consignado", "consignado_inss": "credito_consignado",
               "credito_pessoal": "credito_pessoal", "parcelamento_fatura": "parcelamento_fatura"}
PREFIXO_ID = {"cobertura_cheque_especial": "cob", "credito_consignado": "con", "credito_pessoal": "cp", "parcelamento_fatura": "par"}


def ids_ofertas(ofertas: dict | None) -> list[str]:
    """Identificadores das opções liberadas (cob_01, con_01, cp_01, par_01), como no contexto da Gi e nos exemplos dela."""
    out, contagem = [], {}
    for op in (ofertas or {}).get("opcoes") or []:
        if op.get("id"):
            out.append(str(op["id"]))
            continue
        tipo = TIPO_OFERTA.get(op.get("produto"), op.get("produto"))
        contagem[tipo] = contagem.get(tipo, 0) + 1
        out.append(f"{PREFIXO_ID.get(tipo, 'of')}_{contagem[tipo]:02d}")
    return out


def id_oferta(ofertas: dict | None, indice: int | None) -> str | None:
    ids = ids_ofertas(ofertas)
    return ids[indice] if indice is not None and 0 <= indice < len(ids) else None


def indice_da_oferta(ofertas: dict | None, oferta_id: str | None) -> int | None:
    ids = ids_ofertas(ofertas)
    return ids.index(oferta_id) if oferta_id in ids else None


def card_formas_de_pagar(estado: dict) -> dict:
    f = estado["fatura"]
    return {"tipo": "formas_de_pagar", "dados": {"texto": "Formas de pagar esta fatura.", "opcoes": [
        {"rotulo": "Total", "valor": f["valor"], "acao": "pagar_total"},
        {"rotulo": "Mínimo", "valor": f["minimo"], "acao": "pagar_minimo"},
        {"rotulo": "Outro valor", "valor": None, "acao": "pagar_outro_valor"}], "contratado": False}}


def registrar_plano(estado: dict, indice: int | None) -> dict:
    """Confirmação determinística (nada contratado): ofertas.plano_de sobre a opção escolhida, números com origem e trace."""
    t0 = time.perf_counter()
    o, m = estado["ofertas"], estado["motor"]
    plano = ofertas_mod.plano_de(o, indice, m)
    estado["plano"] = plano
    estado["historico_contratacoes"].append({"caminho": plano["caminho"], "anomes": plano["anomes_inicio"]})
    nums = [{"valor": v, "origem": plano["origem"][k]} for k, v in plano.items() if k in plano["origem"] and not isinstance(v, bool)]
    adicionar_numeros(estado, nums)
    registrar_trace(estado, "ofertas.plano_de", {"opcao": plano["produto"]}, "plano registrado no state; nada contratado (depende de aprovação)",
                    [{"valor": plano["parcela"], "origem": "ofertas.plano_de:parcela"}, {"valor": plano["teto_cartao_mes"], "origem": "ofertas.plano_de:teto_cartao_mes"}],
                    (time.perf_counter() - t0) * 1000, etapa="confirmar")
    return plano


def _card_confirmacao(estado: dict, t: dict) -> dict:
    o, rec = estado["ofertas"], _recomendada(estado)
    p = estado.get("plano") or {}
    if p and rec and p.get("produto") != rec.get("produto"):
        rec = next((op for op in o["opcoes"] if op.get("produto") == p.get("produto")), rec)
    return {"tipo": "confirmacao", "dados": {"resumo": t["confirmacao"], "plano": estado["plano"], "opcao": rec, "teto_cartao_mes": p.get("teto_cartao_mes", o["teto_cartao_mes"]),
                                             "aviso": AVISO_CONTRATO, "contratado": False, "proximo_passo": "Te acompanho nas próximas três faturas."}}


# ----------------------------------------------------------------------------- checagens em código (antes e depois do validador; nos dois modos)
_RE_NUM_TEXTO = re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\b\d{1,3}(?:,\d{1,2})?%|\bdia\s\d{1,2}\b|\b\d{1,3}\s(?:dias?|parcelas?|vezes|meses|faturas?)\b", re.IGNORECASE)


def _numeros_no_texto(texto: str) -> list[str]:
    return [m.group(0) for m in _RE_NUM_TEXTO.finditer(texto or "")]


def _numero_valido(trecho: str, numeros: list[dict]) -> bool:
    return not guardiao.conferir(trecho, numeros)["removidos"]


def termos_proibidos(texto: str) -> list[str]:
    t = (texto or "").lower()
    return [termo for termo in TERMOS_PROIBIDOS if termo in t]


def oferta_conhecida(estado: dict, r: dict, oid: str | None) -> bool:
    """oferta_id aponta para uma opção liberada: pelo id do servidor, pelo id gravado na opção, pela lista que o runtime
    devolveu (ofertas_liberadas) ou pelo nome do produto contido no id (o runtime pode usar outro esquema de ids)."""
    if oid is None:
        return True
    o = estado.get("ofertas") or {}
    if oid in ids_ofertas(o):
        return True
    lib = r.get("ofertas_liberadas") or ((r.get("contexto") or {}).get("ofertas_liberadas") if isinstance(r.get("contexto"), dict) else None) or []
    if any(isinstance(x, dict) and x.get("id") == oid for x in lib):
        return True
    return any(op.get("produto") and str(op["produto"]) in str(oid) for op in (o.get("opcoes") or []))


def checagens_borda(r: dict, estado: dict, modo: str, gatilho: str | None, numeros: list[dict] | None = None) -> dict:
    """Tabela das Notas da Gi, aplicada na borda da API a qualquer resposta (do runtime ou montada aqui).

    Devolve um dict por checagem com 'ok' e detalhe. Quando uma checagem que pede mensagem segura falha (oferta,
    consentimento, termos no texto já sem regeneração possível), a resposta é trocada pela mensagem segura aqui mesmo.
    A regeneração fica no runtime; aqui o que falha vira mensagem segura e é registrado no painel."""
    numeros = numeros if numeros is not None else estado["numeros_validados"]
    msgs = [m for m in (r.get("mensagens") or []) if m.get("papel") == "agente"]
    textos = [m.get("texto") or "" for m in msgs]
    acao = r.get("acao") if r.get("acao") in ACOES_AGENTE else "nenhuma"
    c: dict = {"formato": {"ok": True, "detalhe": "resposta estruturada (mensagens, acao, oferta_id, numeros_citados)"}}

    # números: cada item citado existe no contexto validado; todo número do texto está citado
    citados = [str(x) for x in (r.get("numeros_citados") or [])]
    no_texto = [n for t in textos for n in _numeros_no_texto(t)]
    citados_ok = [x for x in citados if _numero_valido(x, numeros)]
    citados_ruins = [x for x in citados if x not in citados_ok]
    nao_citados = [n for n in no_texto if citados and n not in citados]
    removidos = list((r.get("guardiao") or {}).get("removidos") or [])
    c["numeros"] = {"ok": not citados_ruins and not removidos, "citados": len(citados), "no_texto": len(no_texto),
                    "sem_origem": citados_ruins + [x for x in removidos if x not in citados_ruins], "fora_de_numeros_citados": nao_citados,
                    "detalhe": "cada número do texto conferido contra numeros_validados (guardião na borda)"}

    # oferta: oferta_id existe entre as liberadas; lista vazia => oferta_id nulo
    ids = ids_ofertas(estado.get("ofertas"))
    oid = r.get("oferta_id")
    oferta_ok = (oid is None) if not ids else oferta_conhecida(estado, r, oid)
    if acao in ACOES_COM_OFERTA and not ids:
        oferta_ok = False
    c["oferta"] = {"ok": oferta_ok, "oferta_id": oid, "liberadas": ids}

    # consentimento: sem ele, nem mostrar_oferta nem abrir_resumo_contrato
    consent_ok = estado.get("consentimento") is True or acao not in ("mostrar_oferta", "abrir_resumo_contrato")
    c["consentimento"] = {"ok": consent_ok, "consentimento": estado.get("consentimento") is True, "acao": acao}

    # tamanho: insight até 160 caracteres; conversa até 3 mensagens
    if modo == "insight":
        tam_ok = all(len(t) <= MAX_INSIGHT for t in textos)
        c["tamanho"] = {"ok": tam_ok, "limite": MAX_INSIGHT, "maior": max((len(t) for t in textos), default=0)}
    else:
        tam_ok = len(msgs) <= MAX_MENSAGENS_CONVERSA
        c["tamanho"] = {"ok": tam_ok, "limite": MAX_MENSAGENS_CONVERSA, "mensagens": len(msgs)}
        if not tam_ok:   # junta o excedente na terceira mensagem (a regeneração é do runtime; na borda só se corrige o formato)
            extra = [m for m in (r.get("mensagens") or []) if m.get("papel") != "agente"]
            juntas = msgs[:2] + [_msg(" ".join(t for t in textos[2:]))]
            r["mensagens"] = juntas + extra
            c["tamanho"]["ajustado"] = True

    # termos proibidos (lista do prompt + 'sujeito a' + produtos fora do plano)
    termos = sorted({t for tx in textos for t in termos_proibidos(tx)} | {t for tx in textos for t in policy.termos_bloqueados(tx)})
    c["termos_proibidos"] = {"ok": not termos, "encontrados": termos}

    # gatilho pagar_outro_valor sem oferta: mensagens vazias e ação nenhuma (R18)
    if gatilho == "pagar_outro_valor" and not ids and textos:
        c["gatilho_pagar_outro_valor"] = {"ok": False, "detalhe": "sem oferta liberada o gatilho não gera texto"}
        r["mensagens"] = [m for m in (r.get("mensagens") or []) if m.get("papel") != "agente"]
        r["acao"] = "nenhuma"
        c["formato"]["ajustado"] = True

    # limite de contato: no máximo uma mensagem proativa (insight do fechamento) por dia
    if modo == "insight":
        c["limite_contato"] = {"ok": int(estado.get("proativas") or 0) <= 1, "proativas_hoje": int(estado.get("proativas") or 0)}

    # o que pede mensagem segura na borda
    segura = (not oferta_ok) or (not consent_ok) or bool(termos) or bool(citados_ruins)
    if segura and textos:
        r["mensagens"] = mensagem_segura(estado) if modo != "insight" else [_msg(insight_seguro(estado)["texto"])]
        r["acao"] = "mostrar_formas_de_pagar"
        r["oferta_id"] = None
        r["numeros_citados"] = insight_seguro(estado)["numeros_citados"]
        r["cards"] = [cd for cd in (r.get("cards") or []) if cd.get("tipo") not in ("comparador", "confirmacao")]
        r["sugestoes"] = [{"rotulo": "Ver formas de pagar", "acao": "ver_formas_de_pagar"}, CHIP_HUMANO]
        guardiao.conferir_resposta(r, numeros)
    c["mensagem_segura"] = bool(segura and textos)
    c["ok"] = all(v.get("ok", True) for k, v in c.items() if isinstance(v, dict))
    return c


def checagens_insight(insight: dict, estado: dict) -> dict:
    """As mesmas checagens sobre o card do cartão (modo insight)."""
    r = {"mensagens": [_msg(insight.get("texto") or "")], "acao": "nenhuma", "oferta_id": None, "cards": [], "sugestoes": [],
         "numeros_citados": list(insight.get("numeros_citados") or []), "guardiao": {"removidos": [], "termos_bloqueados": []}}
    guardiao.conferir_resposta(r, estado["numeros_validados"])
    c = checagens_borda(r, estado, "insight", "fechamento")
    if c.get("mensagem_segura"):
        insight.update(insight_seguro(estado))
        insight["gerado_por"] = "codigo (mensagem segura)"
    else:
        insight["texto"] = r["mensagens"][0]["texto"]
    return c


def validador_ausente(motivo: str) -> dict:
    return {"aplicado": False, "aprovado": None, "violacoes": [], "orientacao_para_regenerar": None,
            "motivo": ("sem LLM: texto fixo montado em código; só as checagens em código se aplicam" if motivo == "sem_llm" else motivo)}


def resumo_validador(estado: dict, taxas: dict, modelo: str | None) -> dict:
    """Totais do painel: turnos com LLM, vereditos, regenerações e mensagens seguras. Config em taxas.yaml (validador) e env."""
    cfg = (taxas.get("validador") if isinstance(taxas.get("validador"), dict) else {}) or {}
    ativo = str(os.environ.get("VALIDADOR_ATIVO", cfg.get("ativo", True))).lower() not in ("0", "false", "nao", "não")
    turnos = estado.get("turnos") or []
    com_llm = [t for t in turnos if t.get("llm")]
    vered = [t.get("validador") or {} for t in com_llm]
    return {
        "ativo": ativo,
        "modelo": (os.environ.get("MODELO_VALIDADOR") or modelo) if estado.get("modo") == "llm" else None,
        "frase": FRASE_VALIDADOR,
        "turnos": len(turnos),
        "turnos_com_llm": len(com_llm),
        "aprovados": sum(1 for v in vered if v.get("aprovado") is True),
        "reprovados": sum(1 for v in vered if v.get("aprovado") is False),
        "sem_veredito": sum(1 for v in vered if v.get("aprovado") is None),
        "regeneracoes": sum(int(t.get("regeneracoes") or 0) for t in turnos),
        "mensagens_seguras": sum(1 for t in turnos if t.get("mensagem_segura")),
        "checagens_reprovadas": sum(1 for t in turnos if isinstance(t.get("checagens"), dict) and t["checagens"].get("ok") is False),
        "nota": ("modo sem LLM: nenhum texto gerado por modelo nesta sessão; as checagens em código valem para os textos fixos"
                 if estado.get("modo") != "llm" else "1 chamada do agente + 1 do validador por turno; no pior caso 1 regeneração"),
    }


# ----------------------------------------------------------------------------- acompanhamento e painel (nunca LLM)
def avancar_mes(estado: dict, fonte, taxas: dict) -> dict:
    plano = estado.get("plano")
    if not plano:
        raise ValueError("sem_plano")
    if plano.get("encerrado"):
        raise ValueError("plano_encerrado")
    anomes = calendario.anomes_soma(estado["mes_simulado"], 1)
    t0 = time.perf_counter()
    c = acompanhar.ciclo(fonte, estado["cliente_id"], plano, anomes, taxas)
    dt = (time.perf_counter() - t0) * 1000
    estado["plano"] = c["plano"]
    estado["mes_simulado"] = anomes
    dados = {k: v for k, v in c.items() if k != "plano"}
    estado["ciclos"].append(dados)
    adicionar_numeros(estado, c["numeros"])
    registrar_trace(estado, "acompanhar.ciclo", {"anomes": anomes, "llm": False}, c["frase"],
                    [{"valor": c["fatura"], "origem": "acompanhar.ciclo:fatura"},
                     {"valor": c["juros_evitados_acumulados"], "origem": "acompanhar.ciclo:juros_evitados_acumulados"},
                     {"valor": c["teto_cartao"], "origem": "acompanhar.ciclo:teto_cartao"}], dt, etapa=f"avancar_mes_{len(estado['ciclos'])}")
    r = {"anomes": c["anomes"], "fatura": c["fatura"], "paga_inteira": c["paga_inteira"], "ciclos_ok": c["ciclos_ok"], "encerrado": c["encerrado"],
         "mensagem": c["frase"], "cards": [{"tipo": "acompanhamento", "dados": dados}],
         "numeros_validados": [dict(n) for n in estado["numeros_validados"]], "guardiao": {"removidos": [], "termos_bloqueados": []},
         "modo": "sem_llm", "simulado": True}
    return guardiao.conferir_resposta(r, estado["numeros_validados"])


def finops(estado: dict, taxas: dict, modelo: str | None) -> dict:
    """Bloco `finops` do painel: custo em USD por papel e por turno, fonte do preço, projeção e teto (server/finops.py ->
    cabe_core.finops sobre config/finops.yaml). `taxas` fica por compatibilidade: o preço não vem mais de taxas.yaml."""
    r = finops_srv.resumo_sessao(estado, modelo)
    r["rotulo_modelo"] = rotulo_modelo(estado["modo"], modelo)
    return r


def rotulo_modelo(modo: str | None, modelo: str | None) -> str:
    return modelo if modo == "llm" and modelo else "nenhum (sem LLM)"


def bloco_painel(estado: dict, taxas: dict, modelo: str | None) -> dict:
    """Campos novos do painel da banca, iguais nos dois modos: um registro por turno e o resumo do validador."""
    return {"turnos": [dict(t) for t in (estado.get("turnos") or [])], "validador": resumo_validador(estado, taxas, modelo),
            "modo_conversa": estado.get("modo_conversa"), "rotulo_modelo": rotulo_modelo(estado.get("modo"), modelo),
            "historico": [dict(h) for h in (estado.get("historico") or [])], "insight": estado.get("insight")}


def painel_juri(estado: dict, fonte, taxas: dict, modelo: str | None) -> dict:
    hist = []
    if estado.get("consentimento") is True:
        hist = fatura_mod.historico(fonte, estado["cliente_id"], ANOMES_FIM_BASE, 12)
    ultimo = estado["ciclos"][-1] if estado["ciclos"] else None
    j = painel_mod.juri({"motor": estado.get("motor"), "ofertas": estado.get("ofertas"), "plano": estado.get("plano"),
                         "ciclos": [ultimo] if ultimo else [], "historico_faturas": hist, "numeros_validados": estado["numeros_validados"]})
    adicionar_numeros(estado, j["numeros"])
    j["numeros_com_origem"] = [dict(n) for n in estado["numeros_validados"]]
    j["simulado_lista"] = j.pop("simulado")
    j["simulado"] = True
    j["consentimento"] = estado.get("consentimento")
    j["finops"] = finops(estado, taxas, modelo)
    j["modo"] = estado["modo"]
    j.update(bloco_painel(estado, taxas, modelo))
    return j


# ----------------------------------------------------------------------------- ponte para o agente (fase 2)
# O runtime (cabe_no_bolso.runtime) é dono da sessão do ADK e expõe, com o mesmo sessao_id desta API:
#   criar_sessao[_async](cliente_id, anomes, persona=None, sessao_id=None, ...) -> dict | {erro, mensagem_cliente}
#   consentir[_async](sessao_id, concedido) -> dict; conversar[_async](sessao_id, cliente_id, anomes, texto_ou_acao, valor=None) -> dict
#   avancar_mes[_async](sessao_id) -> dict; trace[_async](sessao_id) -> list; painel[_async](sessao_id) -> dict; estado_bruto(sessao_id) -> dict
# Em qualquer falha (import, exceção, dict com "erro"), a sessão passa para o modo sem LLM e continua daqui, com o estado
# sincronizado a partir de estado_bruto: a banca nunca vê um erro de modelo.
_runtime = {"modulo": None, "tentado_em": 0.0}
ACOES_RUNTIME_TEXTO = {  # modo tools: ações que o agente com ferramentas não conhece viram a frase do cliente (entrada é dado, não instrução)
    "confirmar_entrada_regular": "Sim, o PIX mensal é renda minha. Pode contar com ele nas contas.",
    "contar_pix": "Sim, o PIX mensal é renda minha. Pode contar com ele nas contas.",
    "nao_contar_pix": "Não, o PIX mensal não é renda. Deixe de fora das contas.",
}


def modo_conversa_configurado() -> str:
    """MODO_CONVERSA: gi | tools | auto | sem_llm. auto deixa o runtime dizer (modo_conversa()); sem ele, tools."""
    return (os.environ.get("MODO_CONVERSA", "auto").strip().lower() or "auto")


def carregar_runtime():
    """Importa cabe_no_bolso.runtime tarde e sem quebrar: se não existir, o servidor fica no modo sem LLM."""
    if modo_conversa_configurado() == "sem_llm":
        return None
    if _runtime["modulo"] is not None:
        return _runtime["modulo"]
    agora = time.monotonic()
    if agora - _runtime["tentado_em"] < 30:
        return None
    _runtime["tentado_em"] = agora
    try:
        mod = importlib.import_module("cabe_no_bolso.runtime")
    except Exception as e:  # ImportError ou erro de inicialização do runtime: não derruba a API
        log.info("cabe_no_bolso.runtime indisponível (%s): modo sem_llm", type(e).__name__)
        return None
    if not any(callable(getattr(mod, n, None)) for n in ("conversar_async", "conversar")):
        return None
    _runtime["modulo"] = mod
    return mod


def modo_atual() -> str:
    return "llm" if carregar_runtime() is not None else "sem_llm"


def modo_conversa_atual() -> str:
    """'gi' | 'tools' | 'sem_llm': o que o servidor vai pedir ao runtime nesta configuração."""
    mod = carregar_runtime()
    if mod is None:
        return "sem_llm"
    cfg = modo_conversa_configurado()
    if cfg in MODOS_LLM:
        return cfg
    fn = getattr(mod, "modo_conversa", None)
    if callable(fn):
        try:
            m = str(fn() or "").strip().lower()
        except Exception:
            m = ""
        if m in MODOS_LLM:
            return m
    padrao = str(getattr(mod, "MODO_CONVERSA_PADRAO", "") or "").lower()
    return padrao if padrao in MODOS_LLM else "tools"


def _kwargs_aceitos(fn, kwargs: dict) -> dict:
    """Só passa ao runtime os argumentos nomeados que a assinatura dele aceita (gatilho, modo, contar_pix...)."""
    if not kwargs:
        return {}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k in params}


async def _rt(mod, nome: str, *args, **kwargs):
    """Chama a versão *_async se existir; senão a síncrona numa thread (ela usa asyncio.run). kwargs só se aceitos."""
    fn = getattr(mod, nome + "_async", None)
    if callable(fn):
        return await fn(*args, **_kwargs_aceitos(fn, kwargs))
    fn = getattr(mod, nome)
    return await run_in_threadpool(fn, *args, **_kwargs_aceitos(fn, kwargs))


def _tem(mod, nome: str) -> bool:
    return any(callable(getattr(mod, n, None)) for n in (nome + "_async", nome))


def _acumular_finops(estado: dict, fin: dict | None) -> dict:
    """Soma o FinOps de um turno ao da sessão e devolve o do turno (chamadas do agente e do validador, tokens, latência).

    Latências por chamada (agente + validador) entram na lista da sessão (p50/p95 do painel); a latência do turno é a
    duração total do turno (duracao_turno_ms) quando o runtime a informa."""
    fin = fin or {}
    ef = estado["finops"]
    for k in CHAVES_FINOPS:
        ef[k] = int(ef.get(k, 0) or 0) + int(fin.get(k, 0) or 0)
    lat = [float(x) for x in (fin.get("latencias_ms") or []) if x is not None]
    lat_val = [float(x) for x in (fin.get("latencias_validador_ms") or []) if x is not None]
    ef["latencias_ms"] = list(ef.get("latencias_ms") or []) + lat + lat_val
    _somar_por_papel(ef, fin.get("chamadas_por_papel"))
    total = fin.get("duracao_turno_ms")
    return {**{k: int(fin.get(k, 0) or 0) for k in CHAVES_FINOPS},
            "latencia_ms": (float(total) if total is not None else (round(sum(lat + lat_val), 1) if (lat or lat_val) else None)),
            "latencias_ms": lat, "latencias_validador_ms": lat_val, "chamadas_por_papel": dict(fin.get("chamadas_por_papel") or {})}


def _somar_por_papel(ef: dict, delta: dict | None) -> None:
    """Soma o FinOps por papel de um turno (agente | validador | regeneracao | insight, vindo do runtime) ao da sessão."""
    if not isinstance(delta, dict) or not delta:
        return
    pp = dict(ef.get("chamadas_por_papel") or {})
    for papel, d in delta.items():
        if not isinstance(d, dict):
            continue
        reg = dict(pp.get(papel) or {"chamadas": 0, "tokens_entrada": 0, "tokens_saida": 0, "latencias_ms": []})
        for k in ("chamadas", "tokens_entrada", "tokens_saida"):
            reg[k] = int(reg.get(k, 0) or 0) + int(d.get(k, 0) or 0)
        reg["latencias_ms"] = list(reg.get("latencias_ms") or []) + [x for x in (d.get("latencias_ms") or []) if x is not None]
        if d.get("modelo"):
            reg["modelo"] = d["modelo"]
        pp[papel] = reg
    ef["chamadas_por_papel"] = pp


def _validador_de(r: dict) -> dict:
    """Normaliza o bloco do validador vindo do runtime (opcional): aprovado, violacoes, orientacao, chamadas."""
    v = r.get("validador")
    if not isinstance(v, dict):
        return {"aplicado": False, "aprovado": None, "violacoes": [], "orientacao_para_regenerar": None,
                "motivo": "o runtime não devolveu veredito neste turno"}
    if v.get("ativo") is False and v.get("aprovado") is None:
        return {"aplicado": False, "aprovado": None, "violacoes": [], "orientacao_para_regenerar": None, "motivo": "validador desligado (VALIDADOR_ATIVO=false)"}
    return {"aplicado": v.get("aprovado") is not None or bool(v.get("erro")), "aprovado": v.get("aprovado"), "violacoes": list(v.get("violacoes") or []),
            "orientacao_para_regenerar": v.get("orientacao_para_regenerar") or v.get("orientacao"),
            "chamadas": int(v.get("chamadas") or (1 if v.get("aprovado") is not None else 0)), "modelo": v.get("modelo"),
            "regeneracoes": int(v.get("regeneracoes") or 0), "mensagem_segura": bool(v.get("mensagem_segura")),
            "motivo": (f"validador indisponível: {str(v.get('erro'))[:120]}" if v.get("erro") and v.get("aprovado") is None
                       else v.get("motivo_mensagem_segura") or v.get("motivo"))}


def _registrar_turno_llm(estado: dict, r: dict, *, acao: str | None, modo: str, gatilho: str | None, checagens: dict, fin_turno: dict) -> dict:
    v = _validador_de(r)
    regen = int(r.get("regeneracoes") or v.get("regeneracoes") or 0)
    segura = bool(r.get("mensagem_segura")) or v.get("mensagem_segura", False) or bool(checagens.get("mensagem_segura"))
    ch = r.get("checagens") if isinstance(r.get("checagens"), dict) else None
    llm = int(fin_turno.get("chamadas_llm") or 0) > 0 or int(fin_turno.get("chamadas_validador") or 0) > 0 or v.get("aplicado", False)
    turno = registrar_turno(estado, acao=acao or "texto", modo=modo if llm else "codigo", gatilho=gatilho, llm=llm, modo_conversa=estado.get("modo_conversa"),
                            checagens={"runtime": ch, **checagens} if ch else checagens, validador=v, regeneracoes=regen, mensagem_segura=segura,
                            acao_agente=r.get("acao"), oferta_id=r.get("oferta_id"), numeros_citados=list(r.get("numeros_citados") or []), **fin_turno)
    return turno


def _falha(estado: dict, ferramenta: str, motivo: str, t0: float | None = None) -> None:
    estado["modo"] = "sem_llm"
    estado["modo_conversa"] = "sem_llm"
    registrar_trace(estado, ferramenta, {}, f"{motivo}; a sessão segue sem LLM", [],
                    None if t0 is None else (time.perf_counter() - t0) * 1000, llm=True, etapa="analise")
    log.warning("%s: %s; sessão %s segue sem LLM", ferramenta, motivo, (estado.get("sessao_id") or "")[:6])


def _espelhar_finops(estado: dict, bruto: dict) -> dict:
    """Contadores do state do agente (chamadas do agente e do validador, tokens, latências) são a fonte: o servidor
    guarda o maior valor. Devolve o que cresceu desde o último espelho (FinOps de um turno feito dentro do runtime)."""
    fin = bruto.get("finops") or {}
    ef = estado["finops"]
    delta = {}
    for k in CHAVES_FINOPS:
        novo = int(fin.get(k, 0) or 0)
        delta[k] = max(0, novo - int(ef.get(k, 0) or 0))
        ef[k] = max(int(ef.get(k, 0) or 0), novo)
    lat = [float(x) for x in (fin.get("latencias_ms") or []) if x is not None] + [float(x) for x in (fin.get("latencias_validador_ms") or []) if x is not None]
    if len(lat) > len(ef.get("latencias_ms") or []):
        ef["latencias_ms"] = lat
    if isinstance(fin.get("chamadas_por_papel"), dict) and fin["chamadas_por_papel"]:
        ef["chamadas_por_papel"] = fin["chamadas_por_papel"]           # o state do agente é a fonte: espelho inteiro
    ef["entradas_bloqueadas"] = max(int(ef.get("entradas_bloqueadas") or 0), int(bruto.get("entradas_bloqueadas") or 0))
    delta["latencia_ms"] = round(sum(lat[len(lat) - max(delta["chamadas_llm"], 0) - max(delta["chamadas_validador"], 0):]), 1) if lat and (delta["chamadas_llm"] or delta["chamadas_validador"]) else None
    return delta


def ultimo_veredito(bruto: dict) -> dict | None:
    """Último item 'validador' do trace do agente ({aprovado, violacoes, orientacao}), para o turno feito dentro do runtime."""
    for t in reversed(bruto.get("trace") or []):
        if isinstance(t, dict) and t.get("ferramenta") == "validador":
            return {"aprovado": t.get("aprovado"), "violacoes": list(t.get("violacoes") or []), "orientacao": t.get("orientacao"), "chamadas": 1}
    return None


async def _sincronizar(estado: dict, mod, com_trace: bool = True, finops: bool = True) -> dict | None:
    """Copia do runtime para o estado local o que a reserva precisa para continuar de onde a conversa parou.

    com_trace=False é o espelho barato usado no modo gi depois de cada turno: análise, plano, escolhas, números e FinOps.
    Devolve o estado bruto do runtime (ou None)."""
    fn = getattr(mod, "estado_bruto", None)
    if not callable(fn):
        return None
    try:
        bruto = await run_in_threadpool(fn, estado["sessao_id"])
    except Exception as e:
        log.warning("estado_bruto falhou (%s); a reserva segue com o estado local", type(e).__name__)
        return None
    if not isinstance(bruto, dict) or not bruto:
        return None
    if not com_trace:
        for k in ("motor", "ofertas", "plano", "mes_simulado", "historico_contratacoes"):
            if bruto.get(k) is not None:
                estado[k] = bruto[k]
        if bruto.get("contar_pix") is not None:
            estado["contar_pix"] = bool(bruto["contar_pix"])
        if bruto.get("recusou_oferta"):
            estado["oferta_recusada"] = True
        if bruto.get("encaminhado"):
            estado["encerrado"] = True
        adicionar_numeros(estado, bruto.get("numeros_validados"))
        estado["_finops_delta"] = _espelhar_finops(estado, bruto) if finops else {}
        return bruto
    if bruto.get("consentimento") is not None:
        estado["consentimento"] = bool(bruto["consentimento"])
    if bruto.get("consentimento_registro"):
        estado["registro_consentimento"] = bruto["consentimento_registro"]
    for k in ("motor", "ofertas", "plano", "mes_simulado", "historico_contratacoes"):
        if bruto.get(k) is not None:
            estado[k] = bruto[k]
    if bruto.get("ciclos"):
        estado["ciclos"] = list(bruto["ciclos"])
    esc = bruto.get("escolha_pagamento")
    if isinstance(esc, dict) and esc.get("acao"):
        estado["escolha"] = {"acao": esc["acao"], "valor": int(esc.get("valor") or 0)}
    if bruto.get("recusou_oferta"):
        estado["oferta_recusada"] = True
    if bruto.get("encaminhado"):
        estado["encerrado"] = True
    if bruto.get("contar_pix") is not None:
        estado["contar_pix"] = bool(bruto["contar_pix"])
    adicionar_numeros(estado, bruto.get("numeros_validados"))
    for t in bruto.get("trace") or []:
        if isinstance(t, dict) and t.get("ferramenta") != "fatura.historico":
            registrar_trace(estado, t.get("ferramenta", "?"), t.get("argumentos"), t.get("resumo", ""), t.get("numeros"),
                            t.get("duracao_ms"), bool(t.get("llm")), t.get("etapa"))
    _espelhar_finops(estado, bruto)
    return bruto


async def rt_sessao(estado: dict, mod) -> None:
    """POST /api/sessao: cria a sessão no runtime com o mesmo sessao_id; em falha, a sessão fica sem LLM."""
    t0 = time.perf_counter()
    try:
        r = await _rt(mod, "criar_sessao", estado["cliente_id"], estado["anomes"], estado.get("persona"), sessao_id=estado["sessao_id"])
    except Exception as e:
        _falha(estado, "runtime.criar_sessao", f"falhou: {type(e).__name__}", t0)
        return
    if not isinstance(r, dict) or r.get("erro"):
        _falha(estado, "runtime.criar_sessao", f"erro: {str((r or {}).get('erro'))[:80]}", t0)
        return
    adicionar_numeros(estado, r.get("numeros_validados"))
    estado["modo"] = "llm"
    registrar_trace(estado, "runtime.criar_sessao", {"app": "cabe_no_bolso"}, "sessão do agente criada (InMemorySessionService, mesmo sessao_id)",
                    [], (time.perf_counter() - t0) * 1000, etapa="sessao")


def garantir_analise_local(estado: dict, fonte, taxas: dict | None) -> None:
    """Modo gi: o servidor precisa de motor/ofertas locais (cards, ids de oferta, checagens, plano em código). Se o runtime
    não os expôs em estado_bruto, ou se contar_pix mudou, recalcula aqui com cabe_core (mesmos números, sem rede)."""
    if fonte is None or taxas is None or estado.get("consentimento") is not True:
        return
    m = estado.get("motor")
    precisa = not m or not estado.get("ofertas")
    if m and estado.get("contar_pix") is not None and bool((m.get("flags") or {}).get("contar_pix")) != bool(estado.get("contar_pix")):
        precisa = True
    if precisa:
        try:
            garantir_analise(estado, fonte, taxas, forcar=True)
        except Exception as e:   # sem dados: a rota já tratou antes; aqui só não derruba a resposta do modelo
            log.warning("análise local falhou (%s)", type(e).__name__)


async def rt_consentir(estado: dict, mod, concedido: bool, fonte=None, taxas: dict | None = None) -> dict | None:
    t0 = time.perf_counter()
    try:
        r = await _rt(mod, "consentir", estado["sessao_id"], bool(concedido))
    except Exception as e:
        _falha(estado, "runtime.consentir", f"falhou: {type(e).__name__}", t0)
        return None
    if not isinstance(r, dict) or r.get("erro"):
        _falha(estado, "runtime.consentir", f"erro: {str((r or {}).get('erro'))[:80]}", t0)
        return None
    estado["consentimento"] = bool(r.get("consentimento", concedido))
    estado["registro_consentimento"] = r.get("registro")
    adicionar_numeros(estado, r.get("numeros_validados"))
    registrar_trace(estado, "registrar_consentimento", {"concedido": bool(concedido), "via": "runtime"},
                    "consentimento no state do agente; before_tool_callback libera as ferramentas de dados", [],
                    (time.perf_counter() - t0) * 1000, etapa="consentimento")
    if concedido:
        # análise feita no runtime; o servidor espelha motor/ofertas para cards e checagens (e, no modo gi, o FinOps do insight feito lá)
        bruto = await _sincronizar(estado, mod, com_trace=False, finops=estado.get("modo_conversa") == "gi")
        delta_fin = dict(estado.pop("_finops_delta", None) or {})
        if estado.get("modo_conversa") == "gi":
            await run_in_threadpool(garantir_analise_local, estado, fonte, taxas)
        ins = r.get("insight") if isinstance(r.get("insight"), dict) else None
        if ins is not None:
            ins.setdefault("gerado_por", "llm" if ("numeros_citados" in ins or ins.get("validador")) else "codigo")
            if ins["gerado_por"] == "llm" and ins.get("validador") is None and bruto:
                ins["validador"] = ultimo_veredito(bruto)   # o insight foi gerado dentro do consentir do runtime (INSIGHT_COM_LLM)
        gerado = None
        if estado.get("modo_conversa") == "gi" and insight_com_llm() and not (ins and ins.get("gerado_por") == "llm"):
            gerado = await rt_insight(estado, mod, "fechamento")   # modo insight, gatilho fechamento; registra o próprio turno
        if gerado is not None:
            ins = gerado
        elif ins is None and estado.get("motor") and estado.get("ofertas"):
            ins = {**_insight(estado), "gerado_por": "codigo", "validador": None}
        if ins is not None and gerado is None:
            estado["proativas"] = int(estado.get("proativas") or 0) + 1
            c = checagens_insight(ins, estado)
            llm = ins.get("gerado_por") == "llm"
            registrar_turno(estado, acao="consentimento", modo="insight", gatilho="fechamento", llm=llm,
                            modo_conversa=estado.get("modo_conversa"), checagens=c, validador=_validador_de(ins), regeneracoes=int(ins.get("regeneracoes") or 0),
                            mensagem_segura=bool(c.get("mensagem_segura") or ins.get("mensagem_segura")),
                            chamadas_llm=int(delta_fin.get("chamadas_llm", 0)) if llm else 0, chamadas_validador=int(delta_fin.get("chamadas_validador", 0)) if llm else 0,
                            tokens_entrada=int(delta_fin.get("tokens_entrada", 0)) if llm else 0, tokens_saida=int(delta_fin.get("tokens_saida", 0)) if llm else 0,
                            latencia_ms=delta_fin.get("latencia_ms") if llm else None,
                            acao_agente=None, oferta_id=None, numeros_citados=list(ins.get("numeros_citados") or []))
        if ins is not None:
            estado["insight"] = ins
            r["insight"] = ins
            r["turno"] = dict(estado["turnos"][-1]) if estado.get("turnos") else None
    r["numeros_validados"] = [dict(n) for n in estado["numeros_validados"]]
    r["modo"] = "llm"
    r["modo_conversa"] = estado.get("modo_conversa")
    return r


def _botao_insight(b) -> dict | None:
    """Botões do formato do prompt (abrir_chat | ver_formas_de_pagar | nenhuma) -> ações da demo."""
    if not isinstance(b, dict) or not b.get("rotulo"):
        return None
    mapa = {"abrir_chat": "ver_opcoes", "ver_formas_de_pagar": "ver_formas_de_pagar", "nenhuma": "nenhuma"}
    return {"rotulo": str(b["rotulo"]), "acao": mapa.get(str(b.get("acao") or "nenhuma"), str(b.get("acao")))}


def insight_com_llm() -> bool:
    """Modo gi: o card do cartão pelo prompt da Gi (modo insight, gatilho fechamento) custa 1 chamada do agente + 1 do
    validador por sessão. INSIGHT_COM_LLM (agent/.env.example) liga; desligado, o texto do card vem de código."""
    return (os.environ.get("INSIGHT_COM_LLM") or "false").strip().lower() in ("1", "true", "sim", "yes", "on")


async def rt_insight(estado: dict, mod, gatilho: str = "fechamento") -> dict | None:
    """Modo insight do prompt da Gi pelo runtime (gancho insight_gi[_async] ou insight[_async]). None => texto de código.

    Aceita a forma do runtime ({insight: {texto, botao, ...}, validador, checagens, finops}) e a forma plana ({texto, ...})."""
    nome = "insight_gi" if _tem(mod, "insight_gi") else "insight" if _tem(mod, "insight") else None
    if nome is None:
        return None
    t0 = time.perf_counter()
    try:
        r = await _rt(mod, nome, estado["sessao_id"], gatilho=gatilho, modo="insight")
    except Exception as e:
        registrar_trace(estado, "runtime." + nome, {"gatilho": gatilho}, f"falhou: {type(e).__name__}; card com o texto seguro", [],
                        (time.perf_counter() - t0) * 1000, llm=True, etapa="consentimento")
        return None
    plano = r.get("insight") if isinstance(r, dict) and isinstance(r.get("insight"), dict) else r
    if not isinstance(r, dict) or r.get("erro") or r.get("nao_enviar") or not isinstance(plano, dict) or not (plano.get("texto") or "").strip():
        registrar_trace(estado, "runtime." + nome, {"gatilho": gatilho}, "sem texto do modelo (erro, limite de contato ou vazio); card com o texto seguro", [],
                        (time.perf_counter() - t0) * 1000, llm=True, etapa="consentimento")
        if isinstance(r, dict) and r.get("finops"):
            _acumular_finops(estado, r.get("finops"))
        return None
    fin = _acumular_finops(estado, r.get("finops"))
    adicionar_numeros(estado, r.get("numeros_validados"))
    validador_bloco = r.get("validador") if isinstance(r.get("validador"), dict) else plano.get("validador")
    ins = {"estado": (plano.get("estado") if plano.get("estado") not in (None, "gi") else ("cabe" if (estado.get("motor") or {}).get("cabe") else "falta")),
           "texto": str(plano["texto"]).strip(), "botao": _botao_insight(plano.get("botao_primario") or plano.get("botao")),
           "botao_secundario": _botao_insight(plano.get("botao_secundario")), "gerado_por": "llm",
           "numeros_citados": list(plano.get("numeros_citados") or []), "validador": validador_bloco,
           "regeneracoes": int(r.get("regeneracoes") or (validador_bloco or {}).get("regeneracoes") or 0),
           "mensagem_segura": bool(r.get("mensagem_segura") or plano.get("mensagem_segura") or (validador_bloco or {}).get("mensagem_segura"))}
    if ins["botao"] and ins["botao"]["acao"] == "nenhuma":
        ins["botao"] = None
    estado["proativas"] = int(estado.get("proativas") or 0) + 1
    c = checagens_insight(ins, estado)
    ch = r.get("checagens") if isinstance(r.get("checagens"), dict) else None
    registrar_turno(estado, acao="consentimento", modo="insight", gatilho=gatilho, llm=True, modo_conversa=estado.get("modo_conversa"),
                    checagens={"runtime": ch, **c} if ch else c, validador=_validador_de({"validador": validador_bloco}), regeneracoes=ins["regeneracoes"],
                    mensagem_segura=ins["mensagem_segura"] or bool(c.get("mensagem_segura")),
                    acao_agente=None, oferta_id=None, numeros_citados=ins["numeros_citados"], **fin)
    registrar_trace(estado, "runtime." + nome, {"gatilho": gatilho, "modo": "insight"}, f"card do cartão pelo modelo ({len(ins['texto'])} caracteres)",
                    [], fin.get("latencia_ms"), llm=True, etapa="consentimento")
    return ins


async def rt_atualizar(estado: dict, mod, delta: dict) -> bool:
    """Espelha no state do agente o que o servidor decidiu em código (gancho opcional atualizar_estado[_async])."""
    if not _tem(mod, "atualizar_estado") or not delta:
        return False
    try:
        await _rt(mod, "atualizar_estado", estado["sessao_id"], delta)
        return True
    except Exception as e:
        log.info("atualizar_estado falhou (%s); o runtime segue sem o espelho", type(e).__name__)
        return False


def deterministica_no_modo_gi(estado: dict, acao: str | None) -> bool:
    """No modo gi, confirmar / falar_com_pessoa / nao_quero / pagar_total são resolvidas em código, sem chamar o modelo."""
    return estado.get("modo_conversa") == "gi" and acao in ACOES_DETERMINISTICAS_GI


def _delta_para_runtime(estado: dict, acao: str | None) -> dict:
    """O que o servidor decidiu em código e o state do agente precisa saber (contexto da próxima chamada)."""
    delta = {}
    if acao == "confirmar" and estado.get("plano"):
        delta.update(plano=estado["plano"], historico_contratacoes=list(estado.get("historico_contratacoes") or []), opcao_confirmada=0)
    if acao == "nao_quero":
        delta["recusou_oferta"] = True
    if acao == "falar_com_pessoa" or estado.get("encerrado"):
        delta["encaminhado"] = {"motivo": "pedido do cliente" if acao == "falar_com_pessoa" else "encaminhado em código"}
    if estado.get("escolha"):
        delta["escolha_pagamento"] = {"acao": estado["escolha"]["acao"], "valor": int(estado["escolha"]["valor"]),
                                      "rotulo": ROTULO_CLIENTE.get(estado["escolha"]["acao"], estado["escolha"]["acao"])}
    return delta


async def rt_pos_deterministica(estado: dict, mod, acao: str | None) -> None:
    """Depois de uma ação resolvida em código no modo gi: espelha o resultado no state do agente (gancho opcional)."""
    await rt_atualizar(estado, mod, _delta_para_runtime(estado, acao))


def _completar_cards(estado: dict, r: dict) -> None:
    """Cards por ação do agente (formato da Gi) quando o runtime não os trouxe: os dados vêm do estado espelhado."""
    acao = r.get("acao") if r.get("acao") in ACOES_AGENTE else "nenhuma"
    tipos = {c.get("tipo") for c in r["cards"]}
    m, o = estado.get("motor"), estado.get("ofertas")
    enviados = estado.setdefault("_cards_enviados", [])
    if acao == "mostrar_oferta" and o and o.get("opcoes") and "comparador" not in tipos:
        if m and "diagnostico" not in enviados:
            r["cards"].append({"tipo": "diagnostico", "dados": m})
        r["cards"].append({"tipo": "comparador", "dados": o})
    elif acao == "mostrar_formas_de_pagar" and "formas_de_pagar" not in tipos and "comparador" not in tipos:
        if m and "diagnostico" not in enviados and estado.get("consentimento") is True:
            r["cards"].append({"tipo": "diagnostico", "dados": m})
        r["cards"].append(card_formas_de_pagar(estado))
    elif acao == "abrir_resumo_contrato" and "confirmacao" not in tipos and o and o.get("opcoes") and m:
        if not estado.get("plano"):
            i = indice_da_oferta(o, r.get("oferta_id"))
            if i is None and r.get("oferta_id"):
                i = next((k for k, op in enumerate(o["opcoes"]) if op.get("produto") and str(op["produto"]) in str(r["oferta_id"])), None)
            registrar_plano(estado, i)
        t = _textos(estado)
        r["cards"].append(_card_confirmacao(estado, t))
        if not r.get("sugestoes"):
            r["sugestoes"] = [CHIP_AVANCAR, CHIP_HUMANO]
    elif acao == "transferir_humano":
        estado["encerrado"] = True
        if "encaminhamento" not in tipos:
            r["cards"].append(_card_encaminhamento("pedido do cliente ou sinal de proteção"))
        r["sugestoes"] = []
    elif acao == "revogar_consentimento":
        estado["consentimento"] = False
        estado["insight"] = insight_seguro(estado)
        registrar_trace(estado, "registrar_consentimento", {"concedido": False, "via": "cliente"}, "análise desligada a pedido do cliente", [], etapa="consentimento")
        if "aviso" not in tipos:
            r["cards"].append({"tipo": "aviso", "dados": {"texto": "Análise desligada. Sem ela, mostro só as formas de pagar.", "contratado": False}})
        r["sugestoes"] = [{"rotulo": "Voltar ao cartão", "acao": "ir_cartao"}, CHIP_HUMANO]
    elif acao == "devolver_ao_iai" and not r.get("sugestoes"):
        r["sugestoes"] = [CHIP_VER, CHIP_HUMANO]
    for c in r["cards"]:
        if c.get("tipo") not in enviados:
            enviados.append(c.get("tipo"))


def finops_config() -> dict:
    """config/finops.yaml (preços e travas), via cabe_core.finops. Sem o arquivo, {} e os padrões do módulo."""
    return finops_core.carregar()


def teto_chamadas_por_sessao() -> int:
    """Teto de chamadas ao modelo por sessão: env CHAMADAS_LLM_POR_SESSAO_MAX > config/finops.yaml > 12."""
    return finops_core.teto_chamadas_por_sessao()


def _resposta_teto(estado: dict, acao: str | None, modo: str, gatilho: str | None) -> dict:
    """Teto de chamadas ao modelo por sessão (FinOps): a partir dele, mensagem segura sem chamar o runtime."""
    r = {"mensagens": mensagem_segura(estado), "cards": [], "sugestoes": [{"rotulo": "Ver formas de pagar", "acao": "ver_formas_de_pagar"}, CHIP_HUMANO],
         "numeros_validados": [dict(n) for n in estado["numeros_validados"]], "guardiao": {"removidos": [], "termos_bloqueados": []},
         "acao": "mostrar_formas_de_pagar", "oferta_id": None, "numeros_citados": insight_seguro(estado)["numeros_citados"], "validador": None,
         "modo": "llm", "modo_conversa": estado.get("modo_conversa"), "gatilho": gatilho, "simulado": True, "mensagem_segura": True}
    guardiao.conferir_resposta(r, estado["numeros_validados"])
    c = checagens_borda(r, estado, modo, gatilho)
    c["teto_chamadas"] = {"ok": False, "limite": teto_chamadas_por_sessao(), "chamadas": int(estado["finops"].get("chamadas_llm", 0)),
                          "detalhe": "teto de chamadas ao modelo por sessão atingido (config/finops.yaml): mensagem segura, sem chamar o modelo"}
    c["ok"] = False
    c["mensagem_segura"] = True
    r["checagens"] = c
    registrar_trace(estado, "finops.teto_sessao", {"limite": c["teto_chamadas"]["limite"]}, c["teto_chamadas"]["detalhe"], [], 0.0, etapa="analise")
    r["turno"] = dict(registrar_turno(estado, acao=acao or "texto", modo=modo, gatilho=gatilho, llm=False, modo_conversa=estado.get("modo_conversa"),
                                      checagens=c, validador=validador_ausente("teto de chamadas por sessão: o modelo não foi chamado"), regeneracoes=0,
                                      mensagem_segura=True, chamadas_llm=0, tokens_entrada=0, tokens_saida=0, latencia_ms=0.0,
                                      acao_agente="mostrar_formas_de_pagar", oferta_id=None, numeros_citados=r["numeros_citados"]))
    return r


def _chips_padrao(estado: dict, acao_agente: str | None) -> list[dict]:
    """Chips quando o runtime não sugere nenhum: por ação do agente e pelo estado da sessão."""
    if estado.get("plano"):
        return [] if estado["plano"].get("encerrado") else [CHIP_AVANCAR, CHIP_HUMANO]
    if acao_agente == "mostrar_oferta" and _recomendada(estado) and not estado.get("oferta_recusada"):
        o = estado["ofertas"]
        return [{"rotulo": "Quero pagar tudo" if o["caminho"] == "cobertura_curta" else "Quero essa opção", "acao": "confirmar"},
                {"rotulo": "Prefiro continuar como está", "acao": "nao_quero", "secundario": True}, CHIP_HUMANO]
    if acao_agente in ("mostrar_formas_de_pagar", "revogar_consentimento"):
        return [{"rotulo": "Ver formas de pagar", "acao": "ver_formas_de_pagar"}, CHIP_HUMANO]
    if acao_agente in ("transferir_humano",):
        return []
    return [CHIP_VER, CHIP_HUMANO]


async def rt_mensagem(estado: dict, mod, *, acao: str | None, texto: str | None, valor: int | None, fonte=None, taxas: dict | None = None) -> dict | None:
    """POST /api/mensagem pelo agente. None => a reserva sem LLM responde (com o estado sincronizado)."""
    acao = normalizar_acao(acao)
    if acao == "pagar_total" or estado.get("encerrado"):
        return None
    gi = estado.get("modo_conversa") == "gi"
    modo, gatilho = GATILHOS.get(acao, ("conversa", None)) if (acao is None or acao in GATILHOS) else ("conversa", None)
    if gi:
        entrada = acao if acao else texto            # o runtime no modo gi recebe a ação (o contexto vai no JSON, não em frase)
    else:
        entrada = ACOES_RUNTIME_TEXTO.get(acao, acao) if acao else texto
    if acao == "pagar_minimo":
        valor = int(estado["fatura"]["minimo"])
    estado["gatilho"] = gatilho
    registrar_historico(estado, "cliente", ROTULO_CLIENTE.get(acao, acao) if acao else "(texto do cliente)")
    if int(estado["finops"].get("chamadas_llm", 0)) >= teto_chamadas_por_sessao() and acao not in ("pagar_minimo", "pagar_outro_valor"):
        return _resposta_teto(estado, acao, modo, gatilho)
    t0 = time.perf_counter()
    try:
        r = await _rt(mod, "conversar", estado["sessao_id"], estado["cliente_id"], estado["anomes"], entrada, valor,
                      gatilho=gatilho, modo=modo, contar_pix=(True if acao == "confirmar_entrada_regular" else False if acao == "nao_contar_pix" else None),
                      historico=list(estado.get("historico") or []))
    except Exception as e:
        _falha(estado, "runtime.conversar", f"falhou: {type(e).__name__}", t0)
        await _sincronizar(estado, mod)
        return None
    if not isinstance(r, dict) or r.get("erro") or not isinstance(r.get("mensagens"), list):
        _falha(estado, "runtime.conversar", f"erro: {str((r or {}).get('erro'))[:80]}", t0)
        await _sincronizar(estado, mod)
        return None
    fin_turno = _acumular_finops(estado, r.get("finops"))
    adicionar_numeros(estado, r.get("numeros_validados"))
    if acao in ("confirmar_entrada_regular", "nao_contar_pix"):
        estado["contar_pix"] = acao == "confirmar_entrada_regular"
    if gi:
        await _sincronizar(estado, mod, com_trace=False)   # o runtime pode ter recalculado (PIX) ou registrado plano/recusa; FinOps pelo maior valor
        estado.pop("_finops_delta", None)
        await run_in_threadpool(garantir_analise_local, estado, fonte, taxas)
    r["numeros_validados"] = [dict(n) for n in estado["numeros_validados"]]
    r.setdefault("cards", [])
    r.setdefault("sugestoes", [])
    r.setdefault("acao", "nenhuma")
    r.setdefault("oferta_id", None)
    r.setdefault("numeros_citados", [])
    r.setdefault("validador", None)
    r["modo"] = "llm"
    r["modo_conversa"] = estado.get("modo_conversa")
    r["gatilho"] = gatilho
    r["simulado"] = True
    # espelho local do que a reserva precisa saber se o runtime cair depois
    for c in r["cards"]:
        d = c.get("dados") or {}
        if c.get("tipo") == "confirmacao" and d.get("plano"):
            estado["plano"] = d["plano"]
        if c.get("tipo") == "encaminhamento" and d.get("status") == "encaminhado":
            estado["encerrado"] = True
    if acao == "nao_quero":
        estado["oferta_recusada"] = True
    if acao in ("pagar_minimo", "pagar_outro_valor"):
        estado["escolha"] = {"acao": acao, "valor": int(estado["fatura"]["minimo"] if acao == "pagar_minimo" else (valor or 0))}
    validos = estado["numeros_validados"] + _numeros_config_runtime(mod)
    guardiao.conferir_resposta(r, validos)
    checagens = checagens_borda(r, estado, modo, gatilho, validos)
    if acao in ("confirmar_entrada_regular", "nao_contar_pix"):   # a análise mudou: diagnóstico e comparador voltam
        estado["_cards_enviados"] = [c for c in estado.get("_cards_enviados") or [] if c not in ("diagnostico", "comparador")]
    _completar_cards(estado, r)
    v_rt = r.get("validador") if isinstance(r.get("validador"), dict) else {}
    if (r.get("mensagem_segura") or v_rt.get("mensagem_segura") or checagens.get("mensagem_segura")) and not estado.get("encerrado"):
        # mensagem segura: os chips não podem convidar a confirmar uma oferta que a mensagem não mostrou
        r["sugestoes"] = [{"rotulo": "Ver formas de pagar", "acao": "ver_formas_de_pagar"}, CHIP_VER, CHIP_HUMANO]
    if not r["sugestoes"] and not estado.get("encerrado"):
        r["sugestoes"] = _chips_padrao(estado, r.get("acao"))
    if _pergunta_pix_pendente(estado) and any("pix" in (m.get("texto") or "").lower() for m in r["mensagens"]) \
            and not any(s.get("acao") == "confirmar_entrada_regular" for s in r["sugestoes"]):
        r["sugestoes"] = CHIPS_PIX + [s for s in r["sugestoes"] if s.get("acao") not in ("confirmar", "contar_pix", "nao_contar_pix")]
    for m in r["mensagens"]:
        if m.get("papel") == "agente":
            registrar_historico(estado, "agente", m.get("texto"))
    r["checagens"] = checagens
    r["turno"] = dict(_registrar_turno_llm(estado, r, acao=acao, modo=modo, gatilho=gatilho, checagens=checagens, fin_turno=fin_turno))
    return r


def _numeros_config_runtime(mod) -> list[dict]:
    """Números de config que o guardião do agente aceita (callbacks.numeros_config), para a segunda passada não divergir."""
    try:
        from cabe_no_bolso import callbacks  # noqa: WPS433
        fn = getattr(callbacks, "numeros_config", None)
        return list(fn()) if callable(fn) else []
    except Exception:
        return []


async def rt_avancar(estado: dict, mod) -> dict | None:
    t0 = time.perf_counter()
    try:
        r = await _rt(mod, "avancar_mes", estado["sessao_id"])
    except Exception as e:
        _falha(estado, "runtime.avancar_mes", f"falhou: {type(e).__name__}", t0)
        await _sincronizar(estado, mod)
        return None
    if not isinstance(r, dict):
        _falha(estado, "runtime.avancar_mes", "resposta inválida", t0)
        await _sincronizar(estado, mod)
        return None
    if r.get("erro") in ("sem_plano", "plano_encerrado"):
        return r  # a rota traduz para 409
    if r.get("erro"):
        _falha(estado, "runtime.avancar_mes", f"erro: {str(r.get('erro'))[:80]}", t0)
        await _sincronizar(estado, mod)
        return None
    adicionar_numeros(estado, r.get("numeros_validados"))
    r["numeros_validados"] = [dict(n) for n in estado["numeros_validados"]]
    r["modo"] = "llm"
    estado["mes_simulado"] = int(r.get("anomes") or estado["mes_simulado"])
    dados = ((r.get("cards") or [{}])[0].get("dados") or {})
    if dados:
        estado["ciclos"].append(dados)
        if isinstance(dados.get("plano"), dict):
            estado["plano"] = dados["plano"]
        elif estado.get("plano") is not None:
            estado["plano"] = {**estado["plano"], "encerrado": bool(r.get("encerrado")), "ciclos_ok": r.get("ciclos_ok", 0)}
    return r


def juntar_traces(do_runtime: list, local: list) -> list:
    """Trace do agente + o que o servidor registrou em código (plano, teto de FinOps, quedas), sem repetir, renumerado."""
    vistos = {(t.get("ferramenta"), t.get("resumo")) for t in do_runtime if isinstance(t, dict)}
    extras = [dict(t) for t in local if (t.get("ferramenta"), t.get("resumo")) not in vistos and t.get("ferramenta") != "fatura.historico"]
    todos = [dict(t) for t in do_runtime if isinstance(t, dict)] + extras
    for i, t in enumerate(todos, 1):
        t["ordem"] = i
    return todos


async def rt_trace(estado: dict, mod) -> list | None:
    try:
        r = await _rt(mod, "trace", estado["sessao_id"])
    except Exception as e:
        _falha(estado, "runtime.trace", f"falhou: {type(e).__name__}")
        await _sincronizar(estado, mod)
        return None
    return list(r) if isinstance(r, list) else None


async def rt_painel(estado: dict, mod, taxas: dict, modelo: str | None) -> dict | None:
    try:
        r = await _rt(mod, "painel", estado["sessao_id"])
    except Exception as e:
        _falha(estado, "runtime.painel", f"falhou: {type(e).__name__}")
        await _sincronizar(estado, mod)
        return None
    if not isinstance(r, dict) or r.get("erro"):
        return None
    adicionar_numeros(estado, r.get("numeros_com_origem"))
    deles = dict(r.get("finops") or {})
    proprio = finops(estado, taxas, modelo)
    r["finops"] = {**deles, **{k: v for k, v in proprio.items() if v is not None and k != "nota"}, "modo": "llm", "modelo": modelo}
    r["finops"]["nota"] = deles.get("nota") or proprio["nota"]
    r["modo"] = "llm"
    r.setdefault("simulado_lista", [])
    r["simulado"] = True
    r.update(bloco_painel(estado, taxas, modelo))
    return r


async def conversar(estado: dict, fonte, taxas: dict, *, acao: str | None, texto: str | None, valor: int | None) -> dict:
    """Resposta sem LLM (reserva). As rotas tentam o runtime antes, quando a sessão está no modo llm."""
    return await run_in_threadpool(responder, estado, fonte, taxas, acao=acao, texto=texto, valor=valor)
