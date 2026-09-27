"""Current bill: minimum, charges if paying the minimum, ways to pay. Pure; owner: S2."""

from decimal import ROUND_HALF_UP, Decimal

from .tipos import Contexto


def centavos(x: Decimal) -> int:
    """Round half-up to the centavo."""
    return int(x.quantize(Decimal(1), ROUND_HALF_UP))


def pmt(principal_c: int, taxa_mes: float, n: int) -> int:
    """Fixed monthly installment (Price table) for principal_c over n months at taxa_mes."""
    i = Decimal(str(taxa_mes))
    return centavos(principal_c * i / (1 - (1 + i) ** -n))


def formas_de_pagar(ctx: Contexto, taxas: dict) -> dict:
    """Return {
        "valor_c", "dia_vencimento", "minimo_c",
        "encargos_se_pagar_minimo_c",       # rotativo taxa_mes x (valor - minimo)
        "parcelamento_fatura": [{"parcelas", "parcela_c", "custo_total_c", "taxa_mes"}],  # one per prazo
        "origem": {key: "gold_contexto_agente" | "taxas.yaml:<produto>" | "core.fatura"},
    }

    minimo_c = round(valor x rotativo.minimo_pct_fatura); encargos = round((valor - minimo) x rotativo.taxa_mes),
    i.e. one month of rotativo on the unpaid part. Parcelamento: PMT on the full bill per configured prazo,
    custo_total = parcela x n - valor. taxa_mes is returned as-is and doubles as the CET (no fees modelled).
    """
    rot, parc = taxas["rotativo"], taxas["parcelamento_fatura"]
    valor = ctx.fatura_estimada_c
    minimo = centavos(valor * Decimal(str(rot["minimo_pct_fatura"])))
    planos = []
    for n in parc["prazos"]:
        parcela = pmt(valor, parc["taxa_mes"], n)
        planos.append(
            {"parcelas": n, "parcela_c": parcela, "custo_total_c": parcela * n - valor, "taxa_mes": parc["taxa_mes"]}
        )
    return {
        "valor_c": valor,
        "dia_vencimento": ctx.dia_vencimento,
        "minimo_c": minimo,
        "encargos_se_pagar_minimo_c": centavos((valor - minimo) * Decimal(str(rot["taxa_mes"]))),
        "parcelamento_fatura": planos,
        "origem": {
            "valor_c": "gold_contexto_agente",
            "dia_vencimento": "gold_contexto_agente",
            "minimo_c": "core.fatura",
            "encargos_se_pagar_minimo_c": "core.fatura",
            "parcelamento_fatura": "core.fatura",
            "parcelamento_fatura.taxa_mes": "taxas.yaml:parcelamento_fatura",
            "parcelamento_fatura.parcelas": "taxas.yaml:parcelamento_fatura",
        },
    }
