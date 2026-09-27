"""Cost of staying in the rotativo, capped by Lei 14.690/2023. Pure; owner: S2."""


def custo_rotativo(restante_c: int, meses: int, taxas: dict) -> dict:
    """Return {"custo_c", "limitado_pelo_teto": bool, "origem"}. Compound monthly at rotativo.taxa_mes,
    total charges never above teto_encargos_pct_divida x restante_c."""
    raise NotImplementedError
