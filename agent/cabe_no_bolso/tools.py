"""Ferramentas do agente: wrappers finos de cabe_core. Nenhuma conta é feita aqui; nenhum número nasce no modelo.

Cada ferramenta:
- lê cliente_id e anomes do estado da sessão (o estado vence o argumento: o modelo nunca escolhe de quem lê);
- chama a função de cabe_core, guarda o dict completo no estado (motor, ofertas, plano) e acrescenta os números
  (com origem) a state["numeros_validados"], o conjunto que o guardião usa para conferir o texto do modelo;
- devolve ao modelo um resumo compacto, com os valores já formatados em R$ (o modelo copia, não calcula).

O consentimento é imposto por callbacks.before_tool (não por instrução): analisar_fatura, listar_ofertas e
detalhar_fatura nunca rodam sem state["consentimento"].
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from functools import lru_cache

from google.adk.tools import ToolContext

from cabe_core import calendario, capacidade, dados, ofertas as ofertas_mod, travas
from cabe_core.dinheiro import brl, coletar_numeros, pct
from cabe_no_bolso import policy

FERRAMENTAS_DE_DADOS = ("analisar_fatura", "listar_ofertas", "detalhar_fatura")
VERSAO_CONSENTIMENTO = "consentimento-v1-2026-09-27"
ESCOPO_CONSENTIMENTO = "últimos 90 dias de conta e cartão; revogável a qualquer momento"
AVISO_SIMULACAO = "simulação: nada é contratado nem pago de verdade"


# ------------------------------------------------------------------ estado e fontes
@lru_cache(maxsize=1)
def fonte() -> dados.Fonte:
    return dados.fonte_padrao()


def _sessao(tool_context: ToolContext, cliente_id: str = "", anomes: int = 0) -> tuple[str, int]:
    """cliente_id/anomes da sessão. O estado vence: o modelo não escolhe de quem lê (entrada é dado, não instrução)."""
    st = tool_context.state
    cid = st.get("cliente_id") or (cliente_id or "").strip()
    am = int(st.get("anomes") or anomes or 0)
    if not cid or not am:
        raise ValueError("sessão sem cliente_id/anomes: a API cria a sessão com POST /api/sessao")
    return cid, am


def taxas_da_sessao(tool_context: ToolContext) -> dict:
    """config/taxas.yaml, com os overrides de simulação da sessão (evals e testes) por cima. Nunca vem do modelo."""
    base = policy.registro_taxas()
    extra = (tool_context.state.get("simulacao") or {}).get("taxas")
    if not extra:
        return base
    t = copy.deepcopy(base)
    _mesclar(t, extra)
    return t


def _mesclar(alvo: dict, extra: dict) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(alvo.get(k), dict):
            _mesclar(alvo[k], v)
        else:
            alvo[k] = v


def liberacao_da_sessao(tool_context: ToolContext, cliente_id: str, taxas: dict) -> dict:
    """Serviço de crédito (simulado por config); a sessão pode sobrescrever (evals)."""
    lib = policy.liberacao_para(cliente_id, taxas)
    extra = (tool_context.state.get("simulacao") or {}).get("liberacao")
    if extra:
        lib = {p: bool(extra.get(p, False)) for p in lib}
    return lib


def registrar_numeros(state, numeros: list[dict]) -> int:
    """Acrescenta {valor, origem} a state['numeros_validados'] sem repetir. Reatribui (o delta do ADK só vê atribuição)."""
    atual = list(state.get("numeros_validados") or [])
    vistos = {(n["valor"], n["origem"]) for n in atual}
    novos = 0
    for n in numeros or []:
        chave = (n["valor"], n["origem"])
        if chave not in vistos:
            vistos.add(chave)
            atual.append({"valor": n["valor"], "origem": n["origem"]})
            novos += 1
    state["numeros_validados"] = atual
    return novos


def _sem_pesados(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("numeros",)}


# ------------------------------------------------------------------ motor (uso interno e ferramenta)
def _rodar_motor(tool_context: ToolContext, contar_pix: bool = False) -> dict:
    cid, am = _sessao(tool_context)
    taxas = taxas_da_sessao(tool_context)
    m = capacidade.motor(fonte(), cid, am, taxas, contar_pix=contar_pix)
    tool_context.state["motor"] = _sem_pesados(m)
    tool_context.state["ferramentas_usadas"] = sorted(set(tool_context.state.get("ferramentas_usadas") or []) | {"analisar_fatura"})
    if tool_context.state.get("ofertas"):
        tool_context.state["ofertas"] = None      # a análise mudou: as ofertas precisam ser refeitas
    registrar_numeros(tool_context.state, m["numeros"])
    return m


def _motor_ou_roda(tool_context: ToolContext) -> dict:
    m = tool_context.state.get("motor")
    return m if m else _rodar_motor(tool_context, bool(tool_context.state.get("contar_pix")))


def _resumo_motor(m: dict, state) -> dict:
    fl = m["flags"]
    esporadicas = [{"origem_da_entrada": e["micro"], "mediana_mensal": brl(e["mediana_mensal"]), "dia_tipico": e.get("dia_tipico"),
                    "tipo": e.get("tipo")} for e in fl.get("entradas_esporadicas") or []]
    atipicos = [{"categoria": g["categoria"], "valor": brl(g["valor"]), "normal": brl(g["mediana"]), "mes": calendario.rotulo(g["anomes"])}
                for g in (fl.get("gastos_atipicos") or [])[:3]]
    pendente = None
    if fl.get("pix_regular") and not fl.get("contar_pix"):
        pendente = (f"Entra um PIX de cerca de {brl(fl['pix_mensal_mediana'])} por mês que não contei como renda. "
                    "Pergunte ao cliente se é renda certa; se ele confirmar, chame analisar_fatura com contar_pix=true.")
    escolha = state.get("escolha_pagamento") or {}
    return {
        "frase": m["frase"],
        "mes": calendario.rotulo(m["anomes"]),
        "fatura": {"valor": brl(m["fatura"]["valor"]), "vencimento_dia": m["fatura"]["vencimento_dia"], "minimo": brl(m["fatura"]["minimo"])},
        "cabe": bool(m["cabe"]),
        "tipo_falta": m["tipo_falta"],
        "falta": brl(m["falta"]),
        "folga": brl(m["folga"]),
        "pode_pagar_agora_pela_conta": brl(max(0, min(m["fatura"]["valor"], m["folga"]))),
        "renda_recorrente": brl(m["renda_recorrente"]),
        "dia_recebimento": m["dia_recebimento"],
        "dias_ate_recebimento": m["dias_ate_recebimento"],
        "fixos": brl(m["fixos"]),
        "essenciais": brl(m["essenciais"]),
        "proximas_faturas_projetadas": [brl(v) for v in m["proximas_faturas"]],
        "caminho_provavel": ("nenhum: só avisar" if m["cabe"] else "cobertura_curta" if m["tipo_falta"] == "pontual" else "parcelamento"),
        "flags": {
            "renda_irregular": bool(fl.get("renda_irregular")),
            "pix_regular_nao_contado": bool(fl.get("pix_regular")) and not bool(fl.get("contar_pix")),
            "entradas_esporadicas": esporadicas,
            "gastos_atipicos": atipicos,
            "usou_cheque_especial_90d": bool(fl.get("uso_cheque_especial_90d")),
        },
        "pergunta_pendente": pendente,
        "escolha_de_pagamento_do_cliente": escolha.get("rotulo") if escolha else None,
        "simulado": AVISO_SIMULACAO,
    }


# ------------------------------------------------------------------ ferramentas expostas ao modelo
def registrar_consentimento(concedido: bool, tool_context: ToolContext) -> dict:
    """Registra a permissão do cliente para ler os últimos 90 dias de conta e cartão (ou a revogação).

    Chame quando o cliente disser sim ou não ao pedido de permissão. Sem permissão registrada, as ferramentas de
    análise não funcionam. Devolve o registro (data, versão do texto, escopo) para auditoria.
    """
    concedido = bool(concedido)
    registro = {"data": datetime.now(timezone.utc).isoformat(timespec="seconds"), "versao_texto": VERSAO_CONSENTIMENTO,
                "escopo": ESCOPO_CONSENTIMENTO, "concedido": concedido}
    tool_context.state["consentimento"] = concedido
    tool_context.state["consentimento_registro"] = registro
    if not concedido:
        tool_context.state["motor"] = None
        tool_context.state["ofertas"] = None
    return {"consentimento": concedido, "registro": registro,
            "proximo_passo": ("agora pode chamar analisar_fatura" if concedido
                              else "sem análise: mostre só as formas de pagar a fatura (total ou mínimo) e não insista"),
            "origem": "tools.registrar_consentimento"}


def analisar_fatura(cliente_id: str = "", anomes: int = 0, contar_pix: bool = False, tool_context: ToolContext = None) -> dict:
    """Analisa a fatura do mês contra os últimos 90 dias (motor de capacidade): folga, falta, tipo da falta e flags.

    Exige consentimento registrado. Use contar_pix=true só depois de o cliente confirmar que o PIX recorrente é renda.
    Devolve a frase que abre a conversa ('faltam R$ X no dia Y e o dinheiro volta em Z dias', 'faltam R$ X todo mês'
    ou 'a fatura cabe') e os valores já formatados. Nunca recalcule nada a partir deles.
    """
    if contar_pix:
        tool_context.state["contar_pix"] = True
    m = _rodar_motor(tool_context, contar_pix=bool(contar_pix or tool_context.state.get("contar_pix")))
    return _resumo_motor(m, tool_context.state)


def listar_ofertas(cliente_id: str = "", anomes: int = 0, tool_context: ToolContext = None) -> dict:
    """Monta as saídas que cabem no mês (da mais barata para a mais cara), cada uma ao lado do custo de continuar no rotativo.

    Exige consentimento. Só devolve opções que cabem na folga, que o serviço de crédito liberou e que custam menos que
    ficar no rotativo; quando não há opção, devolve encaminhar_humano=true e as formas de pagar a própria fatura.
    Se recomendada for null, não ofereça crédito.
    """
    cid, am = _sessao(tool_context)
    m = _motor_ou_roda(tool_context)
    taxas = taxas_da_sessao(tool_context)
    lib = liberacao_da_sessao(tool_context, cid, taxas)
    hist = list(tool_context.state.get("historico_contratacoes") or [])
    o = ofertas_mod.montar(m, m["grupo"], taxas, lib, hist)
    tool_context.state["ofertas"] = _sem_pesados(o)
    tool_context.state["ferramentas_usadas"] = sorted(set(tool_context.state.get("ferramentas_usadas") or []) | {"listar_ofertas"})
    registrar_numeros(tool_context.state, o["numeros"])
    return _resumo_ofertas(o)


def _resumo_opcao(i: int, op: dict) -> dict:
    r = {
        "indice": i,
        "produto": op["produto"],
        "como_explicar": op["rotulo_cliente"],
        "taxa_mes": pct(op["taxa_mes"]),
        "taxa": op["rotulo_taxa"],
        "parcela": brl(op["parcela"]),
        "custo_total": brl(op["custo_total"]),
        "custo_se_ficar_no_rotativo_no_mesmo_prazo": brl(op["custo_rotativo_mesmo_horizonte"]),
        "termina_em": calendario.rotulo(op["termina_em"]),
        "condicao": op.get("condicao", "depende de aprovação"),
        "requer_confirmacao_humana": bool(op.get("requer_confirmacao_humana")),
    }
    if op.get("dias") is not None:
        r["dias_de_uso"] = op["dias"]
        r["paga_de_volta_em"] = op.get("quitado_em")
    else:
        r["n_parcelas"] = op["n_parcelas"]
    return r


def _resumo_ofertas(o: dict) -> dict:
    c = o["continuar_no_rotativo"]
    return {
        "caminho": o["caminho"],
        "motivo": o["motivo"],
        "pagar_agora_pela_conta": brl(o["pagar_agora"]),
        "valor_que_falta": brl(o["valor_financiado"]),
        "opcoes": [_resumo_opcao(i, op) for i, op in enumerate(o["opcoes"])],
        "recomendada": o["recomendada"],
        "continuar_no_rotativo": {
            "sobre_o_que_falta": brl(c["valor"]), "custo_1_mes": brl(c["custo_1_mes"]), "custo_maximo_por_lei": brl(c["custo_ate_teto"]),
            "taxa_mes": pct(c["taxa_mes"]),
            "se_pagar_so_o_minimo": {"paga_agora": brl(c["se_pagar_minimo"]["paga_agora"]), "fica_para_tras": brl(c["se_pagar_minimo"]["nao_pago"]),
                                     "juros_do_proximo_mes": brl(c["se_pagar_minimo"]["custo_1_mes"])},
        },
        "cabe_no_cartao_ate_a_proxima_fatura": brl(o["teto_cartao_mes"]),
        "encaminhar_humano": bool(o["encaminhar_humano"]),
        "requer_confirmacao_humana": bool(o["requer_confirmacao_humana"]),
        "formas_de_pagar_a_fatura": [{"rotulo": f["rotulo"], "valor": brl(f["valor"])} for f in o["opcoes_da_fatura"]],
        "simulado": AVISO_SIMULACAO,
    }


def detalhar_fatura(cliente_id: str = "", anomes: int = 0, tool_context: ToolContext = None) -> dict:
    """Explica por que a fatura veio nesse valor: maiores categorias do mês de compras e parcelas em curso.

    Exige consentimento. Fatos, sem julgamento: o agente mostra o que pesou, nunca comenta hábitos.
    """
    m = _motor_ou_roda(tool_context)
    comp = m["flags"]["composicao_fatura"]
    tool_context.state["ferramentas_usadas"] = sorted(set(tool_context.state.get("ferramentas_usadas") or []) | {"detalhar_fatura"})
    return {
        "fatura": brl(m["fatura"]["valor"]),
        "compras_de": calendario.rotulo(comp["anomes_compras"]),
        "total_compras_no_cartao": brl(comp["total_compras"]),
        "maiores_categorias": [{"categoria": c["categoria"], "valor": brl(c["valor"]), "pct_da_fatura": f"{c['pct']}%"} for c in comp["categorias"][:4]],
        "parcelas_em_curso": {"quantidade": comp["parcelas_em_curso"]["quantidade"], "valor": brl(comp["parcelas_em_curso"]["valor"])},
        "mes_tipico_de_cartao": brl(comp["mediana_compras"]) if comp.get("mediana_compras") else None,
        "vezes_acima_do_normal": comp.get("vezes_acima_do_normal"),
        "gastos_atipicos": [{"categoria": g["categoria"], "valor": brl(g["valor"]), "normal": brl(g["mediana"])} for g in (m["flags"].get("gastos_atipicos") or [])[:3]],
        "simulado": AVISO_SIMULACAO,
    }


def _valores_conhecidos(state) -> dict[str, int]:
    """Valores em centavos que a sessão já validou e que fazem sentido para 'continuar no rotativo'."""
    m = state.get("motor") or {}
    o = state.get("ofertas") or {}
    esc = state.get("escolha_pagamento") or {}
    fat = (m.get("fatura") or {})
    out = {}
    if fat:
        out["fatura"] = int(fat["valor"])
        out["nao_pago_se_pagar_o_minimo"] = int(fat["valor"]) - int(fat["minimo"])
    if m.get("falta") is not None:
        out["falta"] = int(m["falta"])
    if o.get("valor_financiado") is not None:
        out["valor_que_falta"] = int(o["valor_financiado"])
    if esc.get("valor") is not None and fat:
        out["nao_pago_pela_escolha_do_cliente"] = max(0, int(fat["valor"]) - int(esc["valor"]))
    return out


def simular_continuar_no_rotativo(nao_pago_reais: float, meses: int = 1, tool_context: ToolContext = None) -> dict:
    """Custo de deixar um valor no rotativo do cartão por N meses (taxa de config/taxas.yaml, teto de 100% do valor).

    nao_pago_reais precisa ser um valor que veio de uma ferramenta desta sessão (fatura, falta, o que fica para trás
    ao pagar o mínimo, ou o não pago da escolha do cliente); valores inventados são recusados.
    """
    taxas = taxas_da_sessao(tool_context)
    rot = taxas["rotativo"]
    centavos = int(round(float(nao_pago_reais) * 100))
    conhecidos = _valores_conhecidos(tool_context.state)
    nome = next((k for k, v in conhecidos.items() if abs(v - centavos) <= 1), None)
    if nome is None:
        return {"erro": "valor não reconhecido: use um valor que veio de uma ferramenta desta sessão",
                "valores_aceitos": {k: brl(v) for k, v in conhecidos.items()}}
    valor = conhecidos[nome]
    meses = max(1, min(int(meses), 12))
    custo = travas.custo_rotativo(valor, float(rot["taxa_mes"]), meses, float(rot.get("teto_encargos_pct", 1.0)))
    res = {"valor": valor, "meses": meses, "taxa_mes": float(rot["taxa_mes"]), "custo": custo, "total_a_pagar": valor + custo,
           "teto_encargos_pct": float(rot.get("teto_encargos_pct", 1.0))}
    registrar_numeros(tool_context.state, coletar_numeros(res, f"simular_continuar_no_rotativo[{nome}]"))
    return {"sobre": nome, "valor": brl(valor), "meses": meses, "taxa_mes": pct(res["taxa_mes"]), "custo": brl(custo),
            "total_a_pagar": brl(valor + custo), "teto": "juros e encargos param em 100% do valor original (lei)",
            "simulado": AVISO_SIMULACAO}


def confirmar_plano(indice_opcao: int, tool_context: ToolContext = None) -> dict:
    """Fecha o plano com a opção que o cliente escolheu (índice em opcoes). Só depois do 'sim' explícito do cliente.

    Nada é contratado de verdade (simulação declarada). Devolve o resumo para repetir ao cliente: paga agora, parcela,
    quantas, custo total, termina em, e quanto ainda cabe no cartão até a próxima fatura.
    """
    o = tool_context.state.get("ofertas")
    if not o or not o.get("opcoes"):
        return {"erro": "não há opção para confirmar: chame listar_ofertas antes e mostre as saídas ao cliente"}
    i = int(indice_opcao)
    if i < 0 or i >= len(o["opcoes"]):
        return {"erro": f"índice inválido; opções disponíveis: 0 a {len(o['opcoes']) - 1}"}
    op = o["opcoes"][i]
    if op.get("requer_confirmacao_humana"):
        enc = policy.encaminhar_humano("opção exige confirmação com uma pessoa (público vulnerável)")
        tool_context.state["encaminhado"] = {"motivo": enc["motivo"], "data": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        return {"contratado": False, "encaminhar_humano": True, "motivo": enc["motivo"]}
    m = tool_context.state.get("motor") or {}
    plano = ofertas_mod.plano_de(o, i, motor=m)
    tool_context.state["plano"] = plano
    tool_context.state["opcao_confirmada"] = i
    hist = list(tool_context.state.get("historico_contratacoes") or [])
    hist.append({"caminho": plano["caminho"], "produto": plano["produto"], "anomes": plano["anomes_inicio"], "rolou_depois": False})
    tool_context.state["historico_contratacoes"] = hist
    tool_context.state["ferramentas_usadas"] = sorted(set(tool_context.state.get("ferramentas_usadas") or []) | {"confirmar_plano"})
    registrar_numeros(tool_context.state, coletar_numeros(plano, "ofertas.plano_de", ignorar=("origem",)))
    r = {
        "contratado": False,
        "aviso": "simulação: o contrato real só existe depois da aprovação e da assinatura no app",
        "caminho": plano["caminho"],
        "produto": plano["produto"],
        "como_explicar": plano["rotulo_cliente"],
        "paga_agora_pela_conta": brl(plano["pagar_agora"]),
        "valor_coberto": brl(plano["valor_financiado"]),
        "parcela": brl(plano["parcela"]),
        "custo_total": brl(plano["custo_total"]),
        "juros_que_deixa_de_pagar": brl(plano["juros_evitados_total"]),
        "termina_em": calendario.rotulo(plano["termina_em"]),
        "cabe_no_cartao_ate_a_proxima_fatura": brl(plano["teto_cartao_mes"]),
        "acompanhamento": "volto a cada fatura, até 3 faturas inteiras seguidas",
    }
    if plano.get("dias"):
        r["dias_de_uso"] = plano["dias"]
    else:
        r["n_parcelas"] = plano["n_parcelas"]
    return r


def encaminhar_humano(motivo: str, tool_context: ToolContext = None) -> dict:
    """Passa a conversa para uma pessoa do time, com o contexto. Use quando não cabe, quando o cliente pede ou quando há angústia.

    Sem produto, sem insistir. Depois de chamar, encerre com uma frase curta e respeitosa.
    """
    enc = policy.encaminhar_humano(str(motivo or "pedido do cliente")[:200])
    tool_context.state["encaminhado"] = {"motivo": enc["motivo"], "data": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    tool_context.state["ferramentas_usadas"] = sorted(set(tool_context.state.get("ferramentas_usadas") or []) | {"encaminhar_humano"})
    return {"encaminhado": True, "motivo": enc["motivo"], "produto": None,
            "texto_sugerido": "Vou te passar para uma pessoa do time, com o que a gente já viu aqui. Você não precisa repetir nada."}


FERRAMENTAS = [registrar_consentimento, analisar_fatura, listar_ofertas, detalhar_fatura, simular_continuar_no_rotativo,
               confirmar_plano, encaminhar_humano]
