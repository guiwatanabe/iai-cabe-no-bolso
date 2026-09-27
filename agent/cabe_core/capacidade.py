"""Motor de capacidade de pagamento (Spec da Gi): janela de 90 dias, folga do mês, falta no vencimento e o tipo da falta.

Folga = renda recorrente - débitos recorrentes (fixos) - essenciais. Falta = fatura - folga (<= 0 cabe).
A fatura do mês é a reconstrução exata (docs/01 §3); o fator 1,33 x compras só PROJETA as próximas faturas.
Nada aqui chama rede ou modelo. Cada número sai com origem 'capacidade.motor:<campo>'.
"""
from __future__ import annotations

from statistics import median

from . import anomalia, calendario, grupo as grupo_mod
from .dinheiro import brl, coletar_numeros, mapa_origem


class DadosIndisponiveis(Exception):
    """Sem extrato ou sem fatura para o cliente/mês. A API traduz para a mensagem de docs/06."""


def _mediana_mensal(lanc: list[dict], meses: list[int], filtro) -> int:
    por_mes = {m: 0 for m in meses}
    for l in lanc:
        if l["anomes"] in por_mes and filtro(l):
            por_mes[l["anomes"]] += l["valor"]
    return int(median(por_mes.values())) if por_mes else 0


def motor(fonte, cliente_id: str, anomes: int, taxas: dict, contar_pix: bool = False) -> dict:
    """Capacidade do mês `anomes` para o cliente, sem olhar meses futuros.

    contar_pix: só True depois de o cliente confirmar que o PIX recorrente é renda (a ferramenta pergunta antes).
    """
    anomes = int(anomes)
    if not calendario.valido(anomes):
        raise ValueError(f"anomes inválido: {anomes}")
    n_janela = int(taxas.get("janela_meses", 3))
    fechados = calendario.janela(anomes, n_janela)
    lanc = fonte.extrato(cliente_id, fechados[0], anomes)
    if not lanc:
        raise DadosIndisponiveis(f"sem extrato para {cliente_id} entre {fechados[0]} e {anomes}")
    faturas = fonte.faturas(cliente_id, anomes, n=13)
    atual = next((f for f in faturas if f["anomes"] == anomes), None)
    if atual is None:
        raise DadosIndisponiveis(f"sem fatura em {anomes} para {cliente_id}")
    anteriores = [f for f in faturas if f["anomes"] < anomes][-12:]

    rot = taxas["rotativo"]
    fixos_micros = set(taxas.get("fixos", {}).get("micros", []))
    essenciais_macros = set(taxas.get("essenciais", {}).get("macros", []))
    recorrentes = tuple(taxas.get("entradas_recorrentes", {}).get("micros", anomalia.RECORRENTES_PADRAO))
    an = taxas.get("anomalia", {})
    cob = taxas.get("cobertura_curta", {})
    fator = float(taxas.get("fator_projecao_fatura", 1.33))

    fatura = int(atual["fatura"])
    minimo = int(round(fatura * float(rot.get("minimo_pct_fatura", 0.15))))
    vencimento_dia = int(atual["dia_vencimento"])

    # renda recorrente e flags de entrada (janela + mês atual: o salário do mês já caiu antes do vencimento)
    r = anomalia.renda(lanc, recorrentes, float(an.get("desvio_renda_irregular", 0.15)))
    renda_recorrente = int(r["mediana_recorrente"])
    pix_mediana = int(r["pix_mensal_mediana"])
    if contar_pix:
        renda_recorrente += pix_mediana
    dia_recebimento = r["dia_recebimento"]

    # compromissos do mês pela conta (compras no cartão ficam de fora: já estão dentro da fatura)
    fixos = _mediana_mensal(lanc, fechados, lambda l: l["tipo"] == "S" and l["micro"] in fixos_micros and not l["cartao"])
    essenciais = _mediana_mensal(
        lanc, fechados,
        lambda l: l["tipo"] == "S" and l["macro"] in essenciais_macros and l["micro"] not in fixos_micros and not l["cartao"],
    )
    folga = renda_recorrente - fixos - essenciais
    falta = max(0, fatura - folga)
    cabe = falta == 0

    dias = calendario.dias_ate_recebimento(vencimento_dia, dia_recebimento, int(cob.get("convencao_dias_mes", 30))) if dia_recebimento else None
    g = grupo_mod.classificar(anteriores, modo_atual=atual["modo"], faixas=_faixas(taxas), rotulos=taxas.get("grupos", {}).get("rotulos"))

    teto_dias = int(cob.get("teto_dias", 25))
    limiar_repete = _faixas(taxas)["rolando"][0]
    if cabe:
        tipo_falta = "nenhuma"
    elif (r["renda_irregular"] or dias is None or dias > teto_dias or falta > renda_recorrente
          or g["roladas_12m"] >= limiar_repete):
        tipo_falta = "estrutural"
    else:
        tipo_falta = "pontual"

    # projeção das próximas 3 faturas: fator x compras no cartão (só aqui o 1,33 entra)
    compras_mes = {m: 0 for m in fechados + [anomes]}
    for l in lanc:
        if l["cartao"] and l["anomes"] in compras_mes:
            compras_mes[l["anomes"]] += l["valor"]
    mediana_compras = int(median(compras_mes.values()))
    proximas = [int(round(fator * compras_mes[anomes])), int(round(fator * mediana_compras)), int(round(fator * mediana_compras))]

    uso_cheque = any(f["modo"] == "integral" and f["juros"] > 0 for f in faturas if f["anomes"] in fechados + [anomes])
    perfil = fonte.perfil(cliente_id)

    if tipo_falta == "nenhuma":
        frase = "a fatura cabe"
    elif tipo_falta == "pontual":
        frase = f"faltam {brl(falta)} no dia {vencimento_dia} e o dinheiro volta em {dias} dias"
    else:
        frase = f"faltam {brl(falta)} todo mês"

    out = {
        "cliente_id": cliente_id,
        "anomes": anomes,
        "janela": {"meses_fechados": fechados, "inicio": fechados[0], "fim": anomes, "lancamentos": len(lanc)},
        "fatura": {"valor": fatura, "vencimento_dia": vencimento_dia, "minimo": minimo, "modo_na_base": atual["modo"],
                   "pago_na_base": int(atual["pago"]), "nao_pago_na_base": max(0, fatura - int(atual["pago"]))},
        "renda_recorrente": renda_recorrente,
        "dia_recebimento": dia_recebimento,
        "fixos": fixos,
        "essenciais": essenciais,
        "folga": folga,
        "falta": falta,
        "cabe": cabe,
        "tipo_falta": tipo_falta,
        "dias_ate_recebimento": dias,
        "proximas_faturas": proximas,
        "fator_projecao": fator,
        "compras_cartao_mes": compras_mes[anomes],
        "compras_cartao_mediana": mediana_compras,
        "grupo": g,
        "perfil": perfil,
        "flags": {
            "renda_irregular": bool(r["renda_irregular"]),
            "entradas_esporadicas": r["esporadicas"],
            "pix_mensal_mediana": pix_mediana,
            "pix_regular": bool(r["pix_regular"]),
            "contar_pix": bool(contar_pix),
            "gastos_atipicos": anomalia.gastos(lanc, anomes, float(an.get("desvio_gasto_atipico", 1.0)),
                                               int(an.get("valor_minimo_gasto_atipico", 10000))),
            "composicao_fatura": anomalia.composicao_fatura(lanc, calendario.anomes_soma(anomes, -1),
                                                            mediana_compras or None),
            "uso_cheque_especial_90d": bool(uso_cheque),
        },
        "frase": frase,
    }
    out["origem"] = mapa_origem(out, "capacidade.motor", ignorar=("origem", "numeros"))
    out["numeros"] = coletar_numeros(out, "capacidade.motor", ignorar=("origem", "numeros"))
    return out


def _faixas(taxas: dict) -> dict:
    g = taxas.get("grupos") or {}
    faixas = {k: v for k, v in g.items() if k in grupo_mod.FAIXAS_PADRAO}
    return faixas or grupo_mod.FAIXAS_PADRAO
