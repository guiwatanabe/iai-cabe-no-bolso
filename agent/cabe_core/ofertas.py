"""Ofertas: caminho pela capacidade (pontual -> cobertura curta; estrutural/rolando -> parcelamento), grupo como trava.

O valor financiado é a FALTA (o que não cabe no mês), não a fatura inteira: crédito é ferramenta, só para o que falta.
Opções sempre da mais barata para a mais cara, cada uma ao lado do custo de continuar no rotativo no mesmo horizonte.
Os produtos de parcelamento são comparados no MESMO prazo de referência (o menor que cabe deixando a reserva do cartão),
para a ordem por custo ser justa; cada opção traz também os outros prazos do produto.
Só entram opções que cabem (parcela <= folga), liberadas e mais baratas que o rotativo; as demais vão para
`descartadas` com os bloqueios (para o trace da banca; a conversa não as menciona).
"""
from __future__ import annotations

from . import calendario, travas
from .dinheiro import coletar_numeros, mapa_origem, pmt

ROTULOS = {
    "cheque_especial": "limite da conta por poucos dias, até o salário cair",
    "consignado_clt": "parcela descontada na folha (consignado)",
    "consignado_inss": "parcela descontada no benefício (consignado INSS)",
    "credito_pessoal": "empréstimo com parcela fixa (crédito pessoal)",
    "parcelamento_fatura": "parcelamento da própria fatura",
}
ORDEM_PADRAO = ["consignado_clt", "consignado_inss", "credito_pessoal", "parcelamento_fatura"]


def _rotulo_taxa(status: str | None) -> str:
    return "confirmada" if status == "confirmada" else "ilustrativa"


def _opcao_cobertura(valor: int, motor: dict, taxas: dict, liberacao: dict) -> dict:
    cob = taxas.get("cobertura_curta", {})
    produto = cob.get("produto", "cheque_especial")
    bloco = taxas.get(produto) if isinstance(taxas.get(produto), dict) else {}
    dias = motor["dias_ate_recebimento"]
    taxa_mes = float(bloco.get("taxa_mes_base") or bloco.get("taxa_mes") or 0)
    dias_mes = int(cob.get("convencao_dias_mes", 30))
    custo = int(round(valor * taxa_mes * (dias or 0) / dias_mes))
    total = valor + custo
    rot = taxas["rotativo"]
    custo_rot = travas.custo_rotativo(valor, float(rot["taxa_mes"]), 1, float(rot.get("teto_encargos_pct", 1.0)))
    teto_dias = int(cob.get("teto_dias", 25))
    cobre = travas.cobre_ate_recebimento(total, motor["renda_recorrente"], dias, teto_dias)
    checks = travas.juntar(
        travas.taxa_disponivel(produto, taxas),
        travas.liberado(produto, liberacao),
        cobre,
        travas.cobertura_disponivel(bool(cob.get("conta_voltou_positivo_simulado", True))),
        travas.mais_barato_que_rotativo(custo, custo_rot),
    )
    dia_rec = motor["dia_recebimento"] or 0
    return {
        "produto": produto,
        "rotulo_cliente": ROTULOS.get(produto, produto),
        "taxa_mes": taxa_mes,
        "taxa_status": bloco.get("status"),
        "rotulo_taxa": _rotulo_taxa(bloco.get("status")),
        "dias": dias,
        "n_parcelas": 1,
        "parcela": total,
        "valor_financiado": valor,
        "custo_total": custo,
        "custo_rotativo_mesmo_horizonte": custo_rot,
        "termina_em": calendario.anomes_soma(motor["anomes"], 1) if dia_rec <= motor["fatura"]["vencimento_dia"] else motor["anomes"],
        "cabe": cobre["permitido"],
        "liberado": bool(liberacao.get(produto)),
        "bloqueios": checks["bloqueios"],
        "ampliacao_limite_pct": float(cob.get("ampliacao_limite_pct", 0.10)),
        "condicao": taxas.get("texto_condicao", "depende de aprovação"),
        "requer_confirmacao_humana": False,
        "quitado_em": f"dia {dia_rec}" if dia_rec else None,
    }


def _linha(valor: int, taxa_mes: float, n: int, folga: int, t_rot: float, teto: float, anomes: int) -> dict:
    parcela = pmt(valor, taxa_mes, n) if taxa_mes else int(round(valor / n))
    custo_total = parcela * n - valor
    custo_rot = travas.custo_rotativo(valor, t_rot, n, teto)
    c = travas.juntar(travas.cabe_no_mes(parcela, folga), travas.mais_barato_que_rotativo(custo_total, custo_rot))
    return {"n_parcelas": n, "parcela": parcela, "custo_total": custo_total, "custo_rotativo_mesmo_horizonte": custo_rot,
            "cabe": travas.cabe_no_mes(parcela, folga)["permitido"], "termina_em": calendario.anomes_soma(anomes, n),
            "bloqueios": c["bloqueios"]}


def _prazos_de(produto: str, taxas: dict) -> list[int]:
    bloco = taxas.get(produto) if isinstance(taxas.get(produto), dict) else {}
    padrao = [int(n) for n in (taxas.get("parcelamento", {}).get("prazos_padrao") or [6, 10, 12])]
    proprios = [int(n) for n in (bloco.get("prazos") or [])]
    return sorted(proprios or padrao)


def _opcoes_parcelamento(valor: int, motor: dict, taxas: dict, liberacao: dict) -> tuple[list[dict], dict]:
    par = taxas.get("parcelamento", {})
    rot = taxas["rotativo"]
    t_rot, teto = float(rot["taxa_mes"]), float(rot.get("teto_encargos_pct", 1.0))
    folga = int(motor["folga"])
    anomes = int(motor["anomes"])
    perfil = motor.get("perfil") or {}
    reserva = int(round(int(motor["renda_recorrente"]) * float(taxas.get("regras_plano", {}).get("reserva_cartao_pct_renda", 0.0))))
    ordem = [p for p in (par.get("ordem_produtos") or ORDEM_PADRAO)
             if p != "seguro_prestamista" or taxas.get("permitir_prestamista", False)]

    def elegivel_base(p: str) -> dict:
        return travas.juntar(travas.taxa_disponivel(p, taxas), travas.perfil_permite(p, perfil), travas.liberado(p, liberacao))

    # prazo de referência: o menor prazo do primeiro produto elegível da cadeia cuja parcela cabe deixando a reserva do cartão
    prioridade = next((p for p in ordem if elegivel_base(p)["permitido"]), None)
    n_ref, criterio = None, "nenhum produto elegível"
    if prioridade:
        taxa_p = float(taxas[prioridade].get("taxa_mes") or 0)
        linhas_p = [_linha(valor, taxa_p, n, folga, t_rot, teto, anomes) for n in _prazos_de(prioridade, taxas)]
        com_reserva = [l for l in linhas_p if not l["bloqueios"] and l["parcela"] <= folga - reserva]
        que_cabem = [l for l in linhas_p if not l["bloqueios"]]
        if com_reserva:
            n_ref, criterio = com_reserva[0]["n_parcelas"], f"menor prazo de {prioridade} que cabe deixando a reserva do cartão"
        elif que_cabem:
            n_ref, criterio = que_cabem[0]["n_parcelas"], f"menor prazo de {prioridade} que cabe na folga"
        else:
            n_ref, criterio = linhas_p[-1]["n_parcelas"], f"maior prazo de {prioridade}: nenhuma parcela cabe"

    out = []
    for produto in ordem:
        base = elegivel_base(produto)
        bloco = taxas.get(produto) if isinstance(taxas.get(produto), dict) else {}
        taxa_mes = float(bloco.get("taxa_mes") or 0)
        prazos = _prazos_de(produto, taxas)
        linhas = [_linha(valor, taxa_mes, n, folga, t_rot, teto, anomes) for n in prazos]
        if n_ref in prazos:
            principal = linhas[prazos.index(n_ref)]
        else:
            maiores = [l for l in linhas if n_ref is not None and l["n_parcelas"] >= n_ref]
            principal = maiores[0] if maiores else linhas[-1]
        out.append({
            "produto": produto,
            "rotulo_cliente": ROTULOS.get(produto, produto),
            "taxa_mes": taxa_mes,
            "taxa_status": bloco.get("status"),
            "rotulo_taxa": _rotulo_taxa(bloco.get("status")),
            "n_parcelas": principal["n_parcelas"],
            "parcela": principal["parcela"],
            "valor_financiado": valor,
            "custo_total": principal["custo_total"],
            "custo_rotativo_mesmo_horizonte": principal["custo_rotativo_mesmo_horizonte"],
            "termina_em": principal["termina_em"],
            "cabe": principal["cabe"],
            "liberado": bool(liberacao.get(produto)),
            "bloqueios": base["bloqueios"] + principal["bloqueios"],
            "outros_prazos": [{k: v for k, v in l.items() if k != "bloqueios"} | {"elegivel": not l["bloqueios"]} for l in linhas],
            "folga_apos_parcela": folga - principal["parcela"],
            "condicao": taxas.get("texto_condicao", "depende de aprovação"),
            "requer_confirmacao_humana": travas.requer_confirmacao_humana(produto, perfil),
        })
    return out, {"prazo_referencia": n_ref, "criterio_prazo": criterio, "produto_prioritario": prioridade, "reserva_cartao": reserva}


def montar(motor: dict, grupo: dict, taxas: dict, liberacao: dict, historico_contratacoes: list[dict] | None = None) -> dict:
    """Decide o caminho e monta as opções que cabem. `grupo` é o dict de grupo.classificar (pode ser motor['grupo'])."""
    g = grupo["grupo"] if isinstance(grupo, dict) else str(grupo)
    anomes = int(motor["anomes"])
    fatura = int(motor["fatura"]["valor"])
    minimo = int(motor["fatura"]["minimo"])
    folga = int(motor["folga"])
    falta = int(motor["falta"])
    valor = falta
    rot = taxas["rotativo"]
    t_rot, teto = float(rot["taxa_mes"]), float(rot.get("teto_encargos_pct", 1.0))
    nao_pago_min = max(0, fatura - minimo)
    continuar = {
        "valor": valor,
        "custo_1_mes": travas.custo_rotativo(valor, t_rot, 1, teto),
        "custo_ate_teto": int(round(valor * teto)),
        "taxa_mes": t_rot,
        "se_pagar_minimo": {"paga_agora": minimo, "nao_pago": nao_pago_min,
                            "custo_1_mes": travas.custo_rotativo(nao_pago_min, t_rot, 1, teto)},
    }
    out = {
        "anomes": anomes,
        "grupo": g,
        "tipo_falta": motor["tipo_falta"],
        "caminho": "nenhum",
        "motivo": "",
        "valor_financiado": valor,
        "pagar_agora": fatura - valor,
        "opcoes": [],
        "descartadas": [],
        "continuar_no_rotativo": continuar,
        "recomendada": None,
        "teto_cartao_mes": max(0, folga),
        "requer_confirmacao_humana": False,
        "encaminhar_humano": False,
        "criterios": {},
        "opcoes_da_fatura": [
            {"rotulo": "pagar o total", "valor": fatura},
            {"rotulo": "pagar o mínimo", "valor": minimo},
        ],
    }

    if motor["cabe"]:
        out["motivo"] = "a fatura cabe no mês: só aviso, sem oferta"
        return _fechar(out)
    if motor["flags"].get("renda_irregular"):
        out.update(motivo="renda irregular: sem previsão confiável de recebimento; opções da fatura e atendimento humano",
                   requer_confirmacao_humana=True, encaminhar_humano=True)
        return _fechar(out)
    if g == "no_limite":
        out.update(motivo="6 ou mais faturas roladas em 12 meses: sem crédito automático; opções da fatura e renegociação com uma pessoa",
                   requer_confirmacao_humana=True, encaminhar_humano=True)
        return _fechar(out)
    reinc = travas.sem_reincidencia(historico_contratacoes, anomes, int(taxas.get("parcelamento", {}).get("reincidencia_meses", 3)))
    if not reinc["permitido"]:
        out.update(motivo=reinc["bloqueios"][0], requer_confirmacao_humana=True, encaminhar_humano=True)
        return _fechar(out)

    cob_grupos = taxas.get("cobertura_curta", {}).get("grupos", ["escorregao"])
    if motor["tipo_falta"] == "pontual" and g in cob_grupos:
        out["caminho"] = "cobertura_curta"
        op = _opcao_cobertura(valor, motor, taxas, liberacao)
        if op["bloqueios"]:
            out["descartadas"].append(op)
            out.update(motivo="falta pontual, mas a cobertura curta não está disponível: opções da fatura e atendimento humano",
                       requer_confirmacao_humana=True, encaminhar_humano=True)
        else:
            out["opcoes"].append(op)
            out["motivo"] = f"falta pontual: o recebimento do dia {motor['dia_recebimento']} cobre a falta em {motor['dias_ate_recebimento']} dias"
    else:
        out["caminho"] = "parcelamento"
        gp = travas.grupo_permite("parcelamento", g, taxas)
        disp = travas.parcelamento_disponivel(historico_contratacoes, anomes, int(taxas.get("parcelamento", {}).get("vezes_por_12_meses", 1)))
        trava = travas.juntar(gp, disp)
        candidatas, criterios = _opcoes_parcelamento(valor, motor, taxas, liberacao)
        out["criterios"] = criterios
        for op in candidatas:
            op["bloqueios"] = trava["bloqueios"] + op["bloqueios"]
            (out["opcoes"] if not op["bloqueios"] else out["descartadas"]).append(op)
        if out["opcoes"]:
            out["motivo"] = ("a falta se repete: parcela fixa que cabe na folga, quita a falta e libera o limite"
                             if g == "rolando" else "falta estrutural: parcela fixa que cabe na folga")
        elif not trava["permitido"]:
            out.update(motivo=trava["bloqueios"][0], requer_confirmacao_humana=True, encaminhar_humano=True)
        else:
            out.update(motivo="nenhuma parcela cabe na folga do mês: sem crédito; renegociação com uma pessoa",
                       requer_confirmacao_humana=True, encaminhar_humano=True)

    out["opcoes"].sort(key=lambda o: (o["custo_total"], o["parcela"]))
    automaticas = [i for i, o in enumerate(out["opcoes"]) if not o["requer_confirmacao_humana"]]
    if automaticas:
        out["recomendada"] = automaticas[0]
        out["teto_cartao_mes"] = max(0, folga - out["opcoes"][automaticas[0]]["parcela"])
    elif out["opcoes"]:
        out["requer_confirmacao_humana"] = True
        out["motivo"] += "; a opção disponível exige confirmação com uma pessoa"
    return _fechar(out)


def _fechar(out: dict) -> dict:
    out["origem"] = mapa_origem(out, "ofertas.montar", ignorar=("origem", "numeros", "descartadas"))
    out["numeros"] = coletar_numeros(out, "ofertas.montar", ignorar=("origem", "numeros", "descartadas"))
    return out


def plano_de(ofertas: dict, indice: int | None = None, motor: dict | None = None) -> dict:
    """Plano confirmado a partir da opção escolhida (índice em opcoes; padrão: recomendada). Estado inicial do acompanhamento."""
    i = ofertas["recomendada"] if indice is None else int(indice)
    if i is None or not ofertas["opcoes"]:
        raise ValueError("não há opção para confirmar")
    op = ofertas["opcoes"][i]
    m = motor or {}
    folga = int(m.get("folga", ofertas["teto_cartao_mes"] + op["parcela"]))
    plano = {
        "caminho": ofertas["caminho"],
        "produto": op["produto"],
        "rotulo_cliente": op["rotulo_cliente"],
        "anomes_inicio": ofertas["anomes"],
        "fatura_original": int((m.get("fatura") or {}).get("valor", ofertas["pagar_agora"] + ofertas["valor_financiado"])),
        "pagar_agora": ofertas["pagar_agora"],
        "valor_financiado": op["valor_financiado"],
        "parcela": op["parcela"],
        "n_parcelas": op["n_parcelas"],
        "dias": op.get("dias"),
        "taxa_mes": op["taxa_mes"],
        "custo_total": op["custo_total"],
        "custo_rotativo_mesmo_horizonte": op["custo_rotativo_mesmo_horizonte"],
        "juros_evitados_total": max(0, op["custo_rotativo_mesmo_horizonte"] - op["custo_total"]),
        "termina_em": op["termina_em"],
        "folga": folga,
        "teto_cartao_mes": max(0, folga - op["parcela"]),
        "proximas_faturas": list(m.get("proximas_faturas", [])),
        "ciclos_total": 0,
        "ciclos_ok": 0,
        "parcelas_pagas": 0,
        "juros_evitados_acumulados": 0,
        "encerrado": False,
    }
    plano["origem"] = mapa_origem(plano, "ofertas.plano_de", ignorar=("origem",))
    return plano
