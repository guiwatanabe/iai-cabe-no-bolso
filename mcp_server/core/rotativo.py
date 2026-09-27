"""Cost of staying in the rotativo, capped by Lei 14.690/2023. Pure; owner: S2."""

from decimal import Decimal

from .fatura import centavos


def custo_rotativo(restante_c: int, meses: int, taxas: dict) -> dict:
    """Return {"custo_c", "limitado_pelo_teto": bool, "origem"}. Compound monthly at rotativo.taxa_mes,
    total charges never above teto_encargos_pct_divida x restante_c.

    custo_c = round(min(restante x ((1 + taxa)^meses - 1), teto x restante)); charges only, principal excluded.
    """
    rot = taxas["rotativo"]
    juros = restante_c * ((1 + Decimal(str(rot["taxa_mes"]))) ** meses - 1)
    teto = restante_c * Decimal(str(rot["teto_encargos_pct_divida"]))
    return {
        "custo_c": centavos(min(juros, teto)),
        "limitado_pelo_teto": juros > teto,
        "origem": {"custo_c": "core.rotativo", "limitado_pelo_teto": "core.rotativo"},
    }
