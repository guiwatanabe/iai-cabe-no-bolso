"""Flags de anomalia sobre a janela de 90 dias: renda irregular (média móvel com lag) e gastos atípicos por categoria.

São flags, não decisões: a renda esporádica (PIX, 13º, PLR) nunca vira renda sem o cliente confirmar
(CLAUDE.md: PIX recebido é ambíguo; perguntar, não assumir).
"""
from __future__ import annotations

from statistics import median

from .dinheiro import mapa_origem

RECORRENTES_PADRAO = ("Salario CLT", "Beneficio INSS")
TIPO_ESPORADICA = {
    "Recebimentos diversos": "pix",
    "13o salario": "decimo_terceiro",
    "Bonus PLR": "plr",
    "Recebimento Aluguel": "aluguel",
}


def _moda(valores: list[int]) -> int | None:
    if not valores:
        return None
    contagem: dict[int, int] = {}
    for v in valores:
        contagem[v] = contagem.get(v, 0) + 1
    return max(sorted(contagem), key=lambda k: contagem[k])


def renda(lancamentos_90d: list[dict], micros_recorrentes: tuple[str, ...] = RECORRENTES_PADRAO,
          desvio_max: float = 0.15) -> dict:
    """Separa entradas recorrentes (salário, INSS) de esporádicas e marca renda irregular.

    metodo 'media_movel_lag': para cada mês da janela, a entrada recorrente do mês é comparada com a média dos meses
    anteriores (lag); desvio acima de desvio_max, mês sem entrada recorrente ou nenhuma entrada recorrente => irregular.
    """
    meses = sorted({l["anomes"] for l in lancamentos_90d})
    entradas = [l for l in lancamentos_90d if l["tipo"] == "E"]
    rec_mes = {m: 0 for m in meses}
    por_micro: dict[str, dict[int, int]] = {}
    dias_rec: list[int] = []
    for l in entradas:
        por_micro.setdefault(l["micro"], {}).setdefault(l["anomes"], 0)
        por_micro[l["micro"]][l["anomes"]] += l["valor"]
        if l["micro"] in micros_recorrentes:
            rec_mes[l["anomes"]] += l["valor"]
            dias_rec.append(l["dia"])

    recorrentes, esporadicas = [], []
    for micro, por_mes in por_micro.items():
        valores = [por_mes[m] for m in sorted(por_mes)]
        dias = [l["dia"] for l in entradas if l["micro"] == micro]
        item = {"micro": micro, "mediana_mensal": int(median(valores)), "meses": len(valores), "total": sum(valores),
                "dia_tipico": _moda(dias)}
        if micro in micros_recorrentes:
            recorrentes.append(item)
        else:
            med = item["mediana_mensal"]
            variacao = max(abs(v - med) for v in valores) / med if med else 1.0
            item.update({
                "tipo": TIPO_ESPORADICA.get(micro, "outro"),
                "regular": len(valores) == len(meses) and variacao <= desvio_max,
                "variacao_max": round(variacao, 3),
            })
            esporadicas.append(item)

    serie = [rec_mes[m] for m in meses]
    desvios = []
    for i in range(1, len(serie)):
        lag = sum(serie[:i]) / i
        desvios.append(round(serie[i] / lag - 1, 3) if lag > 0 else None)
    positivos = [v for v in serie if v > 0]
    irregular = (not positivos) or any(v == 0 for v in serie) or any(d is None or abs(d) > desvio_max for d in desvios)

    pix = next((e for e in esporadicas if e["tipo"] == "pix"), None)
    out = {
        "renda_irregular": bool(irregular),
        "mediana_recorrente": int(median(positivos)) if positivos else 0,
        "dia_recebimento": _moda(dias_rec),
        "recorrentes": recorrentes,
        "esporadicas": sorted(esporadicas, key=lambda e: -e["total"]),
        "pix_mensal_mediana": pix["mediana_mensal"] if pix else 0,
        "pix_regular": bool(pix and pix["regular"]),
        "serie_recorrente": {str(m): rec_mes[m] for m in meses},
        "desvios_lag": desvios,
        "metodo": "media_movel_lag",
    }
    out["origem"] = mapa_origem(out, "anomalia.renda", ignorar=("origem",))
    return out


def gastos(lancamentos_90d: list[dict], anomes_atual: int | None = None, desvio_min: float = 1.0,
           valor_min: int = 10000, ignorar_micros: tuple[str, ...] = ("Pagamento de fatura", "Juros pagos")) -> list[dict]:
    """Categorias (macro) em que o gasto do mês atual passou de (1 + desvio_min) x mediana dos meses anteriores.

    Devolve [{categoria, valor, mediana, desvio_pct, anomes, origem}], do maior desvio para o menor.
    Gasto = saídas (tipo S) fora do pagamento da fatura e dos juros; compras no cartão entram na categoria delas.
    """
    meses = sorted({l["anomes"] for l in lancamentos_90d})
    if not meses:
        return []
    atual = anomes_atual or meses[-1]
    anteriores = [m for m in meses if m < atual]
    por_macro: dict[str, dict[int, int]] = {}
    for l in lancamentos_90d:
        if l["tipo"] != "S" or l["micro"] in ignorar_micros:
            continue
        por_macro.setdefault(l["macro"], {}).setdefault(l["anomes"], 0)
        por_macro[l["macro"]][l["anomes"]] += l["valor"]
    out = []
    for macro, por_mes in por_macro.items():
        valor = por_mes.get(atual, 0)
        if valor < valor_min or not anteriores:
            continue
        med = int(median([por_mes.get(m, 0) for m in anteriores]))
        if med == 0:
            desvio = None
        else:
            desvio = (valor - med) / med
            if desvio < desvio_min:
                continue
        item = {"categoria": macro, "valor": valor, "mediana": med, "desvio_pct": None if desvio is None else int(round(desvio * 100)),
                "anomes": atual}
        item["origem"] = mapa_origem(item, "anomalia.gastos")
        out.append(item)
    return sorted(out, key=lambda x: -(x["desvio_pct"] if x["desvio_pct"] is not None else 10**9))


def composicao_fatura(lancamentos: list[dict], anomes_compras: int, mediana_compras: int | None = None) -> dict:
    """O que compôs a fatura: compras no cartão do mês anterior por categoria e parcelas em curso.

    Responde "por que minha fatura veio tão alta?" (spec, cenário C) sem julgamento: fatos por categoria.
    """
    compras = [l for l in lancamentos if l["cartao"] and l["anomes"] == anomes_compras]
    total = sum(l["valor"] for l in compras)
    por_cat: dict[str, int] = {}
    for l in compras:
        por_cat[l["macro"]] = por_cat.get(l["macro"], 0) + l["valor"]
    categorias = [{"categoria": k, "valor": v, "pct": int(round(100 * v / total)) if total else 0}
                  for k, v in sorted(por_cat.items(), key=lambda kv: -kv[1])]
    parcelas = [l for l in compras if (l.get("parcela_total") or 0) > 1]
    out = {
        "anomes_compras": anomes_compras,
        "total_compras": total,
        "categorias": categorias[:5],
        "parcelas_em_curso": {"quantidade": len(parcelas), "valor": sum(l["valor"] for l in parcelas)},
        "mediana_compras": mediana_compras,
        "vezes_acima_do_normal": round(total / mediana_compras, 1) if mediana_compras else None,
    }
    out["origem"] = mapa_origem(out, "anomalia.composicao_fatura")
    return out
