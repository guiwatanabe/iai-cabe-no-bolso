"""Contexto JSON do modo Gi (Notas da Gi, p. 3-4): tudo já calculado por cabe_core, números já formatados como texto.

montar(...) -> {contexto, indice, numeros, motor, ofertas, ids}
  contexto : o JSON que entra em {{contexto_json}} (modo, gatilho, consentimento, cliente, fatura, capacidade,
             parcelas_em_curso, faturas_abaixo_do_total_12m, credito_usado_12m, ofertas_liberadas, mudar_vencimento,
             reincidencia_pos_credito, acompanhamento, transacoes_90d, entrada_regular_a_confirmar, historico_conversa)
  indice   : texto formatado -> {valor (centavos/int/fração), origem} para as checagens e para numeros_validados
  numeros  : [{valor, origem}] para state["numeros_validados"] (a cadeia de rastreabilidade não muda de modo)
  ids      : oferta_id -> índice em ofertas["opcoes"] (confirmar_plano continua pelo núcleo)

Nenhuma aritmética aqui: capacidade.motor, ofertas.montar, travas.custo_rotativo e acompanhar.ciclo fazem as contas.
Sem consentimento, só a fatura do mês (o que o app já mostra): capacidade, ofertas, transações e PIX ficam nulos.
historico_conversa fica vazio no prompt de sistema: o histórico vai pelos eventos da sessão do ADK (entrada é dado,
nunca entra no prompt de sistema).
"""
from __future__ import annotations

import re

from cabe_core import calendario, capacidade, fatura as fatura_mod, ofertas as ofertas_mod, travas
from cabe_core.dinheiro import brl, pct
from cabe_no_bolso import checagens, policy

MODOS = ("insight", "conversa")
GATILHOS = ("fechamento", "pagar_outro_valor", "pergunta_cliente", "acompanhamento")
TIPO_OFERTA = {"cheque_especial": "cobertura_cheque_especial", "consignado_clt": "credito_consignado", "consignado_inss": "credito_consignado",
               "credito_pessoal": "credito_pessoal", "parcelamento_fatura": "parcelamento_fatura"}
PREFIXO_ID = {"cobertura_cheque_especial": "cob", "credito_consignado": "con", "credito_pessoal": "cp", "parcelamento_fatura": "par"}
TIPO_FALTA = {"pontual": "pontual", "estrutural": "recorrente", "nenhuma": "nenhuma"}
HISTORICO_MAX = 8


# ------------------------------------------------------------------ formatação (só na borda)
def _dia(n) -> str:
    return f"dia {int(n)}"


def _dias(n) -> str:
    n = int(n)
    return f"{n} dia" if n == 1 else f"{n} dias"


def _meses(n) -> str:
    n = int(n)
    return f"{n} mês" if n == 1 else f"{n} meses"


def _cet(taxa_mes: float, status: str | None) -> str:
    base = f"{pct(float(taxa_mes))} ao mês"
    return base if status == "confirmada" else f"{base}, taxa ilustrativa"


class _Indice:
    """Acumula texto formatado -> {valor, origem} sem repetir; devolve também a lista para numeros_validados."""

    def __init__(self):
        self.mapa: dict[str, dict] = {}
        self.numeros: list[dict] = []
        self._vistos: set[tuple] = set()

    def reg(self, texto: str, valor, origem: str) -> str:
        if isinstance(valor, bool) or valor is None:
            return texto
        if texto not in self.mapa:
            self.mapa[texto] = {"valor": valor, "origem": origem}
        chave = (valor, origem)
        if chave not in self._vistos:
            self._vistos.add(chave)
            self.numeros.append({"valor": valor, "origem": origem})
        return texto

    def reais(self, centavos: int, origem: str) -> str:
        return self.reg(brl(int(centavos)), int(centavos), origem)


def _constantes(ix: _Indice, taxas: dict) -> None:
    """Constantes de config/taxas.yaml que o agente pode citar (as mesmas de callbacks.numeros_config)."""
    janela = int(taxas.get("janela_meses", 3))
    ix.reg(_dias(janela * 30), janela * 30, "config:taxas.yaml:janela_dias")
    meta = int(taxas.get("regras_plano", {}).get("faturas_inteiras_para_encerrar", 3))
    ix.reg(f"{meta} faturas", meta, "config:taxas.yaml:regras_plano.faturas_inteiras_para_encerrar")
    ix.reg("12 meses", 12, "config:taxas.yaml:historico_meses")


# ------------------------------------------------------------------ blocos do contexto
def _bloco_fatura(ix: _Indice, fat: dict, taxas: dict, prefixo: str, encargos_min: int | None, origem_encargos: str) -> dict:
    valor, venc, minimo = int(fat["valor"]), int(fat["vencimento_dia"]), int(fat["minimo"])
    if encargos_min is None:
        rot = taxas["rotativo"]
        encargos_min = travas.custo_rotativo(fatura_mod.nao_pago(valor, minimo), float(rot["taxa_mes"]), 1, float(rot.get("teto_encargos_pct", 1.0)))
        origem_encargos = "travas.custo_rotativo:encargos_se_pagar_minimo"
    return {
        "valor": ix.reais(valor, f"{prefixo}:fatura.valor"),
        "vencimento": ix.reg(_dia(venc), venc, f"{prefixo}:fatura.vencimento_dia"),
        "pagamento_minimo": ix.reais(minimo, f"{prefixo}:fatura.minimo"),
        "encargos_se_pagar_minimo": ix.reais(int(encargos_min), origem_encargos),
        "formas_de_pagar": [
            {"forma": "total", "valor": ix.reais(valor, f"{prefixo}:fatura.valor")},
            {"forma": "mínimo", "valor": ix.reais(minimo, f"{prefixo}:fatura.minimo")},
        ],
    }


def _bloco_capacidade(ix: _Indice, m: dict) -> dict:
    p = "capacidade.motor"
    rec = None
    if m.get("dia_recebimento"):
        rec = {"valor": ix.reais(m["renda_recorrente"], f"{p}:renda_recorrente"), "data": ix.reg(_dia(m["dia_recebimento"]), int(m["dia_recebimento"]), f"{p}:dia_recebimento")}
    out = {
        "saldo_previsto_no_vencimento": ix.reais(m["folga"], f"{p}:folga"),
        "proximo_recebimento": rec,
        "folga_mensal": ix.reais(m["folga"], f"{p}:folga"),
        "falta_prevista": ix.reais(m["falta"], f"{p}:falta"),
        "tipo_de_falta": TIPO_FALTA.get(m["tipo_falta"], m["tipo_falta"]),
    }
    if m.get("dias_ate_recebimento") is not None:
        out["dias_ate_proximo_recebimento"] = ix.reg(_dias(m["dias_ate_recebimento"]), int(m["dias_ate_recebimento"]), f"{p}:dias_ate_recebimento")
    return out


def _ofertas_liberadas(ix: _Indice, o: dict, m: dict) -> tuple[list[dict], dict[str, int]]:
    out, ids = [], {}
    contagem: dict[str, int] = {}
    for i, op in enumerate(o.get("opcoes") or []):
        tipo = TIPO_OFERTA.get(op["produto"], op["produto"])
        contagem[tipo] = contagem.get(tipo, 0) + 1
        oid = f"{PREFIXO_ID.get(tipo, 'of')}_{contagem[tipo]:02d}"
        p = f"ofertas.montar:opcoes[{i}]"
        item = {
            "id": oid,
            "tipo": tipo,
            "nome": op["rotulo_cliente"],
            "parcela": ix.reais(op["parcela"], f"{p}.parcela"),
            "parcelas": int(op["n_parcelas"]),
            "prazo": (ix.reg(_dias(op["dias"]), int(op["dias"]), f"{p}.dias") if op.get("dias") is not None
                      else ix.reg(_meses(op["n_parcelas"]), int(op["n_parcelas"]), f"{p}.n_parcelas")),
            "custo_total": ix.reais(op["custo_total"], f"{p}.custo_total"),
            "cet": ix.reg(_cet(op["taxa_mes"], op.get("taxa_status")), float(op["taxa_mes"]), f"{p}.taxa_mes"),
            "amplia_limite": False,
            "novo_limite": None,
            "termina_em": calendario.rotulo(op["termina_em"]),
            "custo_se_ficar_nos_juros_do_cartao": ix.reais(op["custo_rotativo_mesmo_horizonte"], f"{p}.custo_rotativo_mesmo_horizonte"),
            "condicao": op.get("condicao", "depende de aprovação"),
            "requer_confirmacao_humana": bool(op.get("requer_confirmacao_humana")),
        }
        ix.reg(str(int(op["n_parcelas"])), int(op["n_parcelas"]), f"{p}.n_parcelas")
        if op.get("quitado_em"):
            item["salario_cobre_em"] = ix.reg(str(op["quitado_em"]), int(m.get("dia_recebimento") or 0), "capacidade.motor:dia_recebimento")
        ids[oid] = i
        out.append(item)
    return out, ids


def _transacoes(ix: _Indice, m: dict) -> list[dict]:
    comp = (m.get("flags") or {}).get("composicao_fatura") or {}
    p = "capacidade.motor:flags.composicao_fatura"
    linhas = []
    for i, c in enumerate((comp.get("categorias") or [])[:5]):
        linhas.append({"categoria": c["categoria"], "valor": ix.reais(c["valor"], f"{p}.categorias[{i}].valor"),
                       "parte_da_fatura": ix.reg(f"{int(c['pct'])}%", int(c["pct"]), f"{p}.categorias[{i}].pct")})
    return linhas


def _entrada_regular(ix: _Indice, m: dict, contar_pix: bool | None) -> dict | None:
    fl = m.get("flags") or {}
    if not fl.get("pix_regular") or contar_pix is not None:
        return None
    esp = next((e for e in fl.get("entradas_esporadicas") or [] if e.get("tipo") == "pix"), None) or {}
    out = {"tipo": "PIX", "descricao": "um PIX que entra todo mês e não foi contado como renda",
           "valor_mensal": ix.reais(fl["pix_mensal_mediana"], "capacidade.motor:flags.pix_mensal_mediana")}
    if esp.get("dia_tipico"):
        out["dia_tipico"] = ix.reg(_dia(esp["dia_tipico"]), int(esp["dia_tipico"]), "anomalia.renda:esporadicas.pix.dia_tipico")
    return out


def _acompanhamento(ix: _Indice, plano: dict | None, ciclo: dict | None, m: dict | None, status_cobertura: str | None) -> dict:
    out = {"cobertura_ativa": None, "credito_ativo": None}
    if not plano:
        return out
    pp = "ofertas.plano_de"
    if plano.get("caminho") == "cobertura_curta":
        dia_rec = (m or {}).get("dia_recebimento")
        status = status_cobertura or ("coberta" if ciclo else "aguardando")
        out["cobertura_ativa"] = {
            "valor": ix.reais(plano["valor_financiado"], f"{pp}:valor_financiado"),
            "data_prevista_salario": ix.reg(_dia(dia_rec), int(dia_rec), "capacidade.motor:dia_recebimento") if dia_rec else None,
            "status": status,
        }
    else:
        pagas, total = int(plano.get("parcelas_pagas", 0)), int(plano.get("n_parcelas", 1))
        venc = ((m or {}).get("fatura") or {}).get("vencimento_dia")
        out["credito_ativo"] = {
            "parcelas_pagas": int(ix.reg(str(pagas), pagas, f"{pp}:parcelas_pagas")),
            "total_parcelas": int(ix.reg(str(total), total, f"{pp}:n_parcelas")),
            "proxima_parcela": {"valor": ix.reais(plano["parcela"], f"{pp}:parcela"),
                                "data": ix.reg(_dia(venc), int(venc), "capacidade.motor:fatura.vencimento_dia") if venc else None},
            "limite_liberado": ix.reais(plano["valor_financiado"], f"{pp}:valor_financiado"),
            "termina_em": calendario.rotulo(plano["termina_em"]),
        }
        ix.reg(_meses(total), total, f"{pp}:n_parcelas")
    return out


# ------------------------------------------------------------------ montagem
def montar(cliente_id: str, anomes: int, modo: str, gatilho: str, consentimento: bool, contar_pix: bool | None = None,
           historico: list[dict] | None = None, *, fonte=None, taxas: dict | None = None, motor: dict | None = None,
           ofertas: dict | None = None, liberacao: dict | None = None, historico_contratacoes: list[dict] | None = None,
           plano: dict | None = None, ciclo: dict | None = None, apelido: str | None = None, escolha_pagamento: dict | None = None,
           status_cobertura: str | None = None, publico_vulneravel: bool | None = None) -> dict:
    """Monta o contexto da Gi para (cliente, mês). Com consentimento roda (ou reaproveita) motor e ofertas de cabe_core."""
    if modo not in MODOS:
        raise ValueError(f"modo inválido: {modo}")
    if gatilho not in GATILHOS:
        raise ValueError(f"gatilho inválido: {gatilho}")
    from cabe_no_bolso import tools  # tarde: evita ciclo com tools -> policy
    fonte = fonte or tools.fonte()
    taxas = taxas or policy.registro_taxas()
    anomes = int(anomes)
    ix = _Indice()
    _constantes(ix, taxas)
    persona = policy.persona_demo(cliente_id, taxas) or {}
    nome = (apelido or persona.get("apelido") or "Cliente").strip().split()[0].capitalize()
    hist_contr = list(historico_contratacoes or [])

    m = o = None
    if consentimento:
        m = motor or capacidade.motor(fonte, cliente_id, anomes, taxas, contar_pix=bool(contar_pix))
        if ofertas is not None:
            o = ofertas
        else:
            lib = liberacao if liberacao is not None else policy.liberacao_para(cliente_id, taxas)
            o = ofertas_mod.montar(m, m["grupo"], taxas, lib, hist_contr or None)
        fat = m["fatura"]
        prefixo = "capacidade.motor"
        encargos = int(o["continuar_no_rotativo"]["se_pagar_minimo"]["custo_1_mes"])
        origem_enc = "ofertas.montar:continuar_no_rotativo.se_pagar_minimo.custo_1_mes"
    else:
        hist = fatura_mod.historico(fonte, cliente_id, anomes, n=1)
        atual = next((f for f in hist if f["anomes"] == anomes), None)
        if atual is None:
            raise capacidade.DadosIndisponiveis(f"sem fatura em {anomes} para {cliente_id}")
        fat = {"valor": int(atual["fatura"]), "vencimento_dia": int(atual["dia_vencimento"]),
               "minimo": fatura_mod.minimo(atual["fatura"], float(taxas["rotativo"].get("minimo_pct_fatura", 0.15)))}
        prefixo, encargos, origem_enc = "fatura.historico", None, ""

    if publico_vulneravel is None:
        publico_vulneravel = ((m or {}).get("perfil") or {}).get("tipo_renda") == "inss" if m else bool(persona.get("publico_vulneravel", False))

    ctx: dict = {
        "modo": modo,
        "gatilho": gatilho,
        "consentimento": bool(consentimento),
        "cliente": {"primeiro_nome": nome, "publico_vulneravel": bool(publico_vulneravel)},
        "fatura": _bloco_fatura(ix, fat, taxas, prefixo, encargos, origem_enc),
        "capacidade": None,
        "parcelas_em_curso": None,
        "faturas_abaixo_do_total_12m": None,
        "credito_usado_12m": False,
        "ofertas_liberadas": [],
        "mudar_vencimento": {"disponivel": False, "datas": []},
        "reincidencia_pos_credito": False,
        "acompanhamento": {"cobertura_ativa": None, "credito_ativo": None},
        "transacoes_90d": None,
        "entrada_regular_a_confirmar": None,
        "historico_conversa": [],
    }
    ids: dict[str, int] = {}
    if m and o:
        ctx["capacidade"] = _bloco_capacidade(ix, m)
        comp = (m.get("flags") or {}).get("composicao_fatura") or {}
        pc = comp.get("parcelas_em_curso") or {}
        ctx["parcelas_em_curso"] = ix.reais(int(pc.get("valor", 0)), "capacidade.motor:flags.composicao_fatura.parcelas_em_curso.valor")
        ctx["faturas_abaixo_do_total_12m"] = int(m["grupo"]["roladas_com_atual"])   # só para decidir; nunca citado (fora do índice)
        ctx["credito_usado_12m"] = not travas.parcelamento_disponivel(hist_contr, anomes, int(taxas.get("parcelamento", {}).get("vezes_por_12_meses", 1)))["permitido"]
        ctx["reincidencia_pos_credito"] = not travas.sem_reincidencia(hist_contr, anomes, int(taxas.get("parcelamento", {}).get("reincidencia_meses", 3)))["permitido"]
        if not plano:
            ctx["ofertas_liberadas"], ids = _ofertas_liberadas(ix, o, m)
        ctx["transacoes_90d"] = _transacoes(ix, m)
        ctx["entrada_regular_a_confirmar"] = _entrada_regular(ix, m, contar_pix)
        ctx["acompanhamento"] = _acompanhamento(ix, plano, ciclo, m, status_cobertura)
    if escolha_pagamento and escolha_pagamento.get("valor") is not None:
        ctx["valor_que_o_cliente_escolheu_pagar"] = ix.reais(int(escolha_pagamento["valor"]), "escolha_pagamento:valor")
    if historico:
        ctx["historico_conversa"] = [{"papel": h.get("papel"), "texto": str(h.get("texto", ""))[:300]} for h in historico[-HISTORICO_MAX:]]
    return {"contexto": ctx, "indice": ix.mapa, "numeros": ix.numeros, "motor": m, "ofertas": o, "ids": ids}


# ------------------------------------------------------------------ índice a partir de um contexto pronto (evals; contextos ilustrativos)
_CHAVES_SEM_CITAR = ("faturas_abaixo_do_total_12m", "historico_conversa", "modo", "gatilho", "id", "tipo", "status", "termina_em")


def indice_de(contexto: dict, prefixo: str = "contexto") -> dict:
    """texto formatado -> {valor, origem} varrendo as folhas de um contexto já montado (sem passar pelo núcleo).

    Serve para os exemplos ilustrativos da Gi em evals/golden_gi.json. faturas_abaixo_do_total_12m fica de fora
    (nunca citado ao cliente).
    """
    mapa: dict[str, dict] = {}

    def visita(x, caminho: str):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in _CHAVES_SEM_CITAR and not isinstance(v, (dict, list)):
                    continue
                visita(v, f"{caminho}.{k}" if caminho else str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x):
                visita(v, f"{caminho}[{i}]")
        elif isinstance(x, bool) or x is None:
            return
        elif isinstance(x, (int, float)):
            mapa.setdefault(str(x), {"valor": x, "origem": f"{prefixo}:{caminho}"})
        elif isinstance(x, str):
            for n in checagens.extrair(x):
                mapa.setdefault(n["texto"], {"valor": n["chave"][1], "origem": f"{prefixo}:{caminho}"})

    visita(contexto, "")
    for texto in ("90 dias",):
        mapa.setdefault(texto, {"valor": 90, "origem": "config:taxas.yaml:janela_dias"})
    return mapa


def numeros_de(indice: dict) -> list[dict]:
    """[{valor, origem}] a partir de um índice (para numeros_validados quando o contexto não veio do núcleo)."""
    vistos, out = set(), []
    for texto, ent in (indice or {}).items():
        v = ent.get("valor")
        if isinstance(v, bool) or v is None:
            continue
        chave = (v, ent["origem"])
        if chave not in vistos:
            vistos.add(chave)
            out.append({"valor": v, "origem": ent["origem"]})
    return out


def resumo_para_trace(ctx: dict) -> str:
    cap = ctx.get("capacidade") or {}
    return (f"modo {ctx.get('modo')}, gatilho {ctx.get('gatilho')}, consentimento {'sim' if ctx.get('consentimento') else 'não'}; "
            f"fatura {ctx.get('fatura', {}).get('valor')} vence {ctx.get('fatura', {}).get('vencimento')}; "
            + (f"falta {cap.get('falta_prevista')} ({cap.get('tipo_de_falta')}); " if cap else "sem análise; ")
            + f"{len(ctx.get('ofertas_liberadas') or [])} oferta(s) liberada(s)"
            + ("; PIX regular a confirmar" if ctx.get("entrada_regular_a_confirmar") else ""))


_RE_CHAVE_ID = re.compile(r"^[a-z]{2,5}_\d{2}$")


def indice_da_oferta(contexto: dict, oferta_id: str | None, ids: dict[str, int] | None = None) -> int | None:
    if not oferta_id:
        return None
    if ids and oferta_id in ids:
        return ids[oferta_id]
    for i, o in enumerate(contexto.get("ofertas_liberadas") or []):
        if o.get("id") == oferta_id:
            return i
    return None


__all__ = ["montar", "indice_de", "numeros_de", "resumo_para_trace", "indice_da_oferta", "MODOS", "GATILHOS", "TIPO_OFERTA"]
