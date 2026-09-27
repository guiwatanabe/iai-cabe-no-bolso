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
"""
from __future__ import annotations

import importlib
import inspect
import logging
import os
import time

from starlette.concurrency import run_in_threadpool

from cabe_core import acompanhar, calendario, capacidade, fatura as fatura_mod, ofertas as ofertas_mod, painel as painel_mod, travas
from cabe_core.dinheiro import brl
from cabe_no_bolso import policy

from . import guardiao
from .sessoes import adicionar_numeros, registrar_trace

log = logging.getLogger("cabe_no_bolso.server")

VERSAO_CONSENTIMENTO = "consentimento-v1-2026-09-27"
ESCOPO_CONSENTIMENTO = "últimos 90 dias de conta e cartão; revogável a qualquer momento"
ESCOPO_RECUSADO = "recusado; o pedido volta no máximo uma vez por mês, nunca no meio do pagamento"
ERRO_DADOS = "Não consegui ler seu extrato agora. Posso tentar de novo ou te passar para uma pessoa."
ANOMES_FIM_BASE = int(os.environ.get("ANOMES_FIM_BASE", "202512"))  # último mês da base: comparativo "2025 real"

ACOES = ("ver_opcoes", "pagar_total", "pagar_minimo", "pagar_outro_valor", "consigo_pagar", "por_que_alta", "confirmar",
         "falar_com_pessoa", "nao_quero", "contar_pix", "nao_contar_pix")

CHIP_HUMANO = {"rotulo": "Falar com uma pessoa", "acao": "falar_com_pessoa", "secundario": True}
CHIP_VER = {"rotulo": "Ver opções", "acao": "ver_opcoes"}
CHIP_AVANCAR = {"rotulo": "Avançar um mês (demo)", "acao": "avancar_mes"}
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


def _resposta(estado: dict, mensagens: list[dict], cards: list[dict], sugestoes: list[dict] | None) -> dict:
    r = {
        "mensagens": mensagens,
        "cards": cards,
        "numeros_validados": [dict(n) for n in estado["numeros_validados"]],
        "guardiao": {"removidos": [], "termos_bloqueados": []},
        "sugestoes": sugestoes if sugestoes is not None else [],
        "modo": "sem_llm",
        "simulado": True,
    }
    return guardiao.conferir_resposta(r, estado["numeros_validados"])


def _msg(texto: str) -> dict:
    return {"papel": "agente", "texto": texto}


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
    return {
        "sessao_id": estado["sessao_id"],
        "modo": estado["modo"],
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
        insight = _insight(estado)
    return {"consentimento": estado["consentimento"], "registro": registro, "insight": insight,
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
        return {"estado": "falta_que_se_repete", "botao": {"rotulo": "Conversar com o ia.i", "acao": "ver_opcoes"},
                "texto": (f"Sua fatura fechou em {fat} e vence dia {venc}. Pelo seu mês, cabe pagar {brl(o['pagar_agora'])}; "
                          f"faltam {brl(m['falta'])}. Dá para juntar o que falta numa parcela que cabe no seu orçamento.")}
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


def _pergunta_pix_pendente(estado: dict) -> bool:
    fl = estado["motor"]["flags"]
    return bool(fl.get("pix_regular")) and estado.get("contar_pix") is None and not estado.get("plano")


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
    """Monta a resposta de /api/mensagem sem modelo de linguagem, só com o núcleo e textos fixos por cenário."""
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
                             [_card_encaminhamento("sinal de angústia na conversa")], [])
        acao = rota
        if acao == "pagar_minimo":
            valor = f["minimo"]

    if acao == "falar_com_pessoa":
        estado["encerrado"] = True
        registrar_trace(estado, "policy.encaminhar_humano", {"motivo": "pedido do cliente"}, "encaminhado a uma pessoa; nenhum produto", [], etapa="analise")
        return _resposta(estado, [_msg(TEXTO_HUMANO)], [_card_encaminhamento("pedido do cliente")], [])

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
                         [{"rotulo": "Voltar ao cartão", "acao": "ir_cartao"}, CHIP_HUMANO])

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

    if acao == "contar_pix" or acao == "nao_contar_pix":
        if estado.get("plano"):
            return _resposta(estado, [_msg("O plano já está confirmado; as contas ficam como estão.")], [], _chips_analise(estado))
        estado["contar_pix"] = acao == "contar_pix"
        if acao == "contar_pix":
            garantir_analise(estado, fonte, taxas, forcar=True)
            t = _textos(estado)
            return _resposta(estado, [_msg(t["pix_refeito"]), _msg(t["proposta"])],
                             [_card_diagnostico(estado), {"tipo": "comparador", "dados": estado["ofertas"]}], _chips_analise(estado))
        return _resposta(estado, [_msg(t["pix_fora"])], [], _chips_analise(estado))

    if acao == "confirmar":
        if estado.get("plano"):
            return _resposta(estado, [_msg("O plano já está confirmado. Vamos acompanhar as próximas faturas.")], [_card_confirmacao(estado, t)], _chips_analise(estado))
        if not rec or estado.get("oferta_recusada"):
            estado["encerrado"] = True
            registrar_trace(estado, "policy.encaminhar_humano", {"motivo": o["motivo"]}, "sem opção que caiba: encaminhado; nenhum produto", [], etapa="analise")
            return _resposta(estado, [_msg(TEXTO_SEM_PLANO)], [_card_encaminhamento("por aqui não há plano que caiba")], [])
        if rec.get("requer_confirmacao_humana"):
            estado["encerrado"] = True
            return _resposta(estado, [_msg(TEXTO_HUMANO)], [_card_encaminhamento("a opção exige confirmação com uma pessoa")], [])
        t0 = time.perf_counter()
        plano = ofertas_mod.plano_de(o, None, m)
        estado["plano"] = plano
        estado["historico_contratacoes"].append({"caminho": plano["caminho"], "anomes": plano["anomes_inicio"]})
        nums = [{"valor": v, "origem": plano["origem"][k]} for k, v in plano.items() if k in plano["origem"] and not isinstance(v, bool)]
        adicionar_numeros(estado, nums)
        registrar_trace(estado, "ofertas.plano_de", {"opcao": plano["produto"]}, "plano registrado no state; nada contratado (depende de aprovação)",
                        [{"valor": plano["parcela"], "origem": "ofertas.plano_de:parcela"}, {"valor": plano["teto_cartao_mes"], "origem": "ofertas.plano_de:teto_cartao_mes"}],
                        (time.perf_counter() - t0) * 1000, etapa="confirmar")
        return _resposta(estado, [_msg(t["fecho"])], [_card_confirmacao(estado, t)], [CHIP_AVANCAR, CHIP_HUMANO])

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
            msgs.append(_msg(t["pix_pergunta"]))
            chips = [{"rotulo": "Sim, é renda", "acao": "contar_pix"}, {"rotulo": "Não é renda", "acao": "nao_contar_pix", "secundario": True}] + chips
        return _resposta(estado, msgs, cards, chips)
    if m["cabe"]:
        return _resposta(estado, [_msg(t["abertura"]), _msg(t["proposta"])], [diagnostico], [CHIP_HUMANO])
    cards = [diagnostico]
    if o.get("encaminhar_humano") and not o.get("opcoes"):
        registrar_trace(estado, "policy.encaminhar_humano", {"motivo": o["motivo"]}, "sem crédito automático: opções da fatura e uma pessoa", [], etapa="analise")
        cards.append({"tipo": "encaminhamento", "dados": {"motivo": "por aqui não há plano que caiba", "contratado": False,
                                                          "texto": "Você pode falar com uma pessoa do time a qualquer momento; ela recebe o contexto desta conversa."}})
    return _resposta(estado, [_msg(t["abertura"]), _msg(t["proposta"])], cards, [CHIP_HUMANO, {"rotulo": "Agora não", "acao": "nao_quero", "secundario": True}])


def _card_confirmacao(estado: dict, t: dict) -> dict:
    o, rec = estado["ofertas"], _recomendada(estado)
    return {"tipo": "confirmacao", "dados": {"resumo": t["confirmacao"], "plano": estado["plano"], "opcao": rec, "teto_cartao_mes": o["teto_cartao_mes"],
                                             "aviso": AVISO_CONTRATO, "contratado": False, "proximo_passo": "Te acompanho nas próximas três faturas."}}


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
    f = estado["finops"]
    lat = sorted(float(x) for x in (f.get("latencias_ms") or []))

    def p(q: float):
        return None if not lat else round(lat[min(len(lat) - 1, int(round(q * (len(lat) - 1))))], 1)

    preco = taxas.get("preco_modelo")
    custo = None
    if (isinstance(preco, dict) and preco.get("fonte") and preco.get("entrada_por_milhao_usd") is not None
            and preco.get("saida_por_milhao_usd") is not None):
        custo = round(f["tokens_entrada"] / 1e6 * float(preco["entrada_por_milhao_usd"])
                      + f["tokens_saida"] / 1e6 * float(preco["saida_por_milhao_usd"]), 6)
    return {
        "chamadas_llm": int(f.get("chamadas_llm", 0)),
        "tokens_entrada": int(f.get("tokens_entrada", 0)),
        "tokens_saida": int(f.get("tokens_saida", 0)),
        "latencia_p50_ms": p(0.5),
        "latencia_p95_ms": p(0.95),
        "custo_estimado": custo,
        "modelo": modelo if estado["modo"] == "llm" else None,
        "modo": estado["modo"],
        "nota": ("custo_estimado fica null porque preco_modelo em config/taxas.yaml não tem fonte" if custo is None else "custo pelo preco_modelo de config/taxas.yaml")
                + ("; modo sem LLM: nenhuma chamada ao modelo nesta sessão" if estado["modo"] == "sem_llm" else "")
                + "; acompanhamento mensal sempre sem LLM",
    }


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
    return j


# ----------------------------------------------------------------------------- ponte para o agente (fase 2)
# O runtime (cabe_no_bolso.runtime) é dono da sessão do ADK e expõe, com o mesmo sessao_id desta API:
#   criar_sessao[_async](cliente_id, anomes, persona=None, sessao_id=None, ...) -> dict | {erro, mensagem_cliente}
#   consentir[_async](sessao_id, concedido) -> dict; conversar[_async](sessao_id, cliente_id, anomes, texto_ou_acao, valor=None) -> dict
#   avancar_mes[_async](sessao_id) -> dict; trace[_async](sessao_id) -> list; painel[_async](sessao_id) -> dict; estado_bruto(sessao_id) -> dict
# Em qualquer falha (import, exceção, dict com "erro"), a sessão passa para o modo sem LLM e continua daqui, com o estado
# sincronizado a partir de estado_bruto: a banca nunca vê um erro de modelo.
_runtime = {"modulo": None, "tentado_em": 0.0}
ACOES_RUNTIME_TEXTO = {  # ações que o runtime não conhece viram a frase do cliente (entrada é dado, não instrução)
    "contar_pix": "Sim, o PIX mensal é renda minha. Pode contar com ele nas contas.",
    "nao_contar_pix": "Não, o PIX mensal não é renda. Deixe de fora das contas.",
}


def carregar_runtime():
    """Importa cabe_no_bolso.runtime tarde e sem quebrar: se não existir, o servidor fica no modo sem LLM."""
    modo = os.environ.get("MODO_CONVERSA", "auto").strip().lower()
    if modo == "sem_llm":
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


async def _rt(mod, nome: str, *args, **kwargs):
    """Chama a versão *_async se existir; senão a síncrona numa thread (ela usa asyncio.run)."""
    fn = getattr(mod, nome + "_async", None)
    if callable(fn):
        return await fn(*args, **kwargs)
    return await run_in_threadpool(getattr(mod, nome), *args, **kwargs)


def _falha(estado: dict, ferramenta: str, motivo: str, t0: float | None = None) -> None:
    estado["modo"] = "sem_llm"
    registrar_trace(estado, ferramenta, {}, f"{motivo}; a sessão segue sem LLM", [],
                    None if t0 is None else (time.perf_counter() - t0) * 1000, llm=True, etapa="analise")
    log.warning("%s: %s; sessão %s segue sem LLM", ferramenta, motivo, (estado.get("sessao_id") or "")[:6])


async def _sincronizar(estado: dict, mod) -> None:
    """Copia do runtime para o estado local o que a reserva precisa para continuar de onde a conversa parou."""
    fn = getattr(mod, "estado_bruto", None)
    if not callable(fn):
        return
    try:
        bruto = await run_in_threadpool(fn, estado["sessao_id"])
    except Exception as e:
        log.warning("estado_bruto falhou (%s); a reserva segue com o estado local", type(e).__name__)
        return
    if not isinstance(bruto, dict) or not bruto:
        return
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
    fin = bruto.get("finops") or {}
    ef = estado["finops"]
    for k in ("chamadas_llm", "tokens_entrada", "tokens_saida"):
        ef[k] = max(int(ef.get(k, 0)), int(fin.get(k, 0) or 0))
    if len(fin.get("latencias_ms") or []) > len(ef.get("latencias_ms") or []):
        ef["latencias_ms"] = list(fin["latencias_ms"])


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


async def rt_consentir(estado: dict, mod, concedido: bool) -> dict | None:
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
    r["numeros_validados"] = [dict(n) for n in estado["numeros_validados"]]
    r["modo"] = "llm"
    return r


async def rt_mensagem(estado: dict, mod, *, acao: str | None, texto: str | None, valor: int | None) -> dict | None:
    """POST /api/mensagem pelo agente. None => a reserva sem LLM responde (com o estado sincronizado)."""
    if acao == "pagar_total" or estado.get("encerrado"):
        return None
    entrada = ACOES_RUNTIME_TEXTO.get(acao, acao) if acao else texto
    t0 = time.perf_counter()
    try:
        r = await _rt(mod, "conversar", estado["sessao_id"], estado["cliente_id"], estado["anomes"], entrada, valor)
    except Exception as e:
        _falha(estado, "runtime.conversar", f"falhou: {type(e).__name__}", t0)
        await _sincronizar(estado, mod)
        return None
    if not isinstance(r, dict) or r.get("erro") or not isinstance(r.get("mensagens"), list):
        _falha(estado, "runtime.conversar", f"erro: {str((r or {}).get('erro'))[:80]}", t0)
        await _sincronizar(estado, mod)
        return None
    fin = r.get("finops") or {}
    ef = estado["finops"]
    for k in ("chamadas_llm", "tokens_entrada", "tokens_saida"):
        ef[k] = int(ef.get(k, 0)) + int(fin.get(k, 0) or 0)
    ef["latencias_ms"] = list(ef.get("latencias_ms") or []) + [float(x) for x in (fin.get("latencias_ms") or [])]
    adicionar_numeros(estado, r.get("numeros_validados"))
    r["numeros_validados"] = [dict(n) for n in estado["numeros_validados"]]
    r.setdefault("cards", [])
    r.setdefault("sugestoes", [])
    r["modo"] = "llm"
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
    return guardiao.conferir_resposta(r, estado["numeros_validados"] + _numeros_config_runtime(mod))


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
    return r


async def conversar(estado: dict, fonte, taxas: dict, *, acao: str | None, texto: str | None, valor: int | None) -> dict:
    """Resposta sem LLM (reserva). As rotas tentam o runtime antes, quando a sessão está no modo llm."""
    return await run_in_threadpool(responder, estado, fonte, taxas, acao=acao, texto=texto, valor=valor)
