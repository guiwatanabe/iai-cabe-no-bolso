"""Current bill: minimum, charges if paying the minimum, ways to pay. Pure; owner: S2."""

from .tipos import Contexto


def formas_de_pagar(ctx: Contexto, taxas: dict) -> dict:
    """Return {
        "valor_c", "dia_vencimento", "minimo_c",
        "encargos_se_pagar_minimo_c",       # rotativo taxa_mes x (valor - minimo)
        "parcelamento_fatura": [{"parcelas", "parcela_c", "custo_total_c", "taxa_mes"}],  # one per prazo
        "origem": {key: "gold_contexto_agente" | "taxas.yaml:<produto>" | "core.fatura"},
    }"""
    raise NotImplementedError
