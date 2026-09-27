"""Offers already released and filtered: the only credit the agent may mention. Pure; owner: S2."""

from decimal import Decimal

from .fatura import centavos, pmt
from .tipos import Contexto, EstadoSessao

TIPOS_CREDITO = {"credito_consignado": "cons_01", "credito_prestamista": "prest_01"}


def ofertas_liberadas(ctx: Contexto, estado: EstadoSessao, taxas: dict) -> list[dict]:
    """Released offers sorted by custo_total_c ascending; [] when none applies.

    Each: {"id", "tipo": "cobertura_cheque_especial" | "credito_consignado" | "credito_prestamista",
           "custo_total_c", "taxa_mes", "prazo_dias" | None, "parcela_c" | None, "parcelas" | None,
           "amplia_limite": bool, "novo_limite_c" | None, "origem"}.

    Empty when: no consent; qtd_pedaladas_12m >= regras.pedaladas_sem_credito; reincidencia_pos_credito;
    confianca_recebimento != "ALTA"; tipo_falta == "SEM_FALTA".
    Cobertura: elegivel_cobertura_curta, liberacao.cobertura, cheque_especial_zerado,
    dias_ate_recebimento <= cobertura_curta.teto_dias, valor_recebimento_tipico_c >= valor_faltante_fatura_c.
    Crédito: elegivel_parcelamento, liberacao.<produto>, not credito_usado_12m, taxa_mes not null,
    parcela_c <= folga_mensal_c (PMT over the configured prazos; keep the cheapest fitting prazo per product).

    Pricing: principal = valor_faltante_fatura_c for every offer. Cobertura (id "cob_01"): simple pro rata,
    custo = round(principal x taxa_mes x dias_ate_recebimento / 30). It widens the overdraft limit only when
    cobertura_curta.limite_cheque_especial_c is configured and the gap exceeds it: novo_limite = principal,
    and above limite x (1 + ampliacao_max_pct_limite) there is no cobertura. Crédito ("cons_01", "prest_01"):
    PMT per prazo, custo_total = parcela x n - principal. taxa_mes is returned as-is and doubles as the CET
    (no fees modelled). publico_vulneravel is not a filter here.
    """
    if (
        not estado.consentimento
        or ctx.qtd_pedaladas_12m >= taxas["regras"]["pedaladas_sem_credito"]
        or estado.reincidencia_pos_credito
        or ctx.confianca_recebimento != "ALTA"
        or ctx.tipo_falta == "SEM_FALTA"
    ):
        return []
    principal = ctx.valor_faltante_fatura_c
    ofertas = []

    cob = taxas["cobertura_curta"]
    limite = cob["limite_cheque_especial_c"]
    amplia = limite is not None and principal > limite
    if (
        ctx.elegivel_cobertura_curta
        and estado.liberacao.cobertura
        and estado.cheque_especial_zerado
        and cob["taxa_mes"] is not None
        and ctx.dias_ate_recebimento <= cob["teto_dias"]
        and ctx.valor_recebimento_tipico_c >= principal
        and not (amplia and principal > limite * (1 + Decimal(str(cob["ampliacao_max_pct_limite"]))))
    ):
        custo = centavos(principal * Decimal(str(cob["taxa_mes"])) * ctx.dias_ate_recebimento / 30)
        ofertas.append(
            {
                "id": "cob_01",
                "tipo": "cobertura_cheque_especial",
                "custo_total_c": custo,
                "taxa_mes": cob["taxa_mes"],
                "prazo_dias": ctx.dias_ate_recebimento,
                "parcela_c": None,
                "parcelas": None,
                "amplia_limite": amplia,
                "novo_limite_c": principal if amplia else None,
                "origem": {
                    "custo_total_c": "core.ofertas",
                    "taxa_mes": "taxas.yaml:cobertura_curta",
                    "prazo_dias": "gold_contexto_agente",
                    "novo_limite_c": "core.ofertas",
                },
            }
        )

    if ctx.elegivel_parcelamento and not estado.credito_usado_12m:
        for tipo, oferta_id in TIPOS_CREDITO.items():
            prod = taxas[tipo]
            if not getattr(estado.liberacao, tipo.removeprefix("credito_")) or prod["taxa_mes"] is None:
                continue
            planos = [(pmt(principal, prod["taxa_mes"], n), n) for n in prod["prazos"]]
            cabem = [(parcela * n - principal, parcela, n) for parcela, n in planos if parcela <= ctx.folga_mensal_c]
            if not cabem:
                continue
            custo, parcela, n = min(cabem)
            ofertas.append(
                {
                    "id": oferta_id,
                    "tipo": tipo,
                    "custo_total_c": custo,
                    "taxa_mes": prod["taxa_mes"],
                    "prazo_dias": None,
                    "parcela_c": parcela,
                    "parcelas": n,
                    "amplia_limite": False,
                    "novo_limite_c": None,
                    "origem": {
                        "custo_total_c": "core.ofertas",
                        "taxa_mes": f"taxas.yaml:{tipo}",
                        "parcela_c": "core.ofertas",
                        "parcelas": f"taxas.yaml:{tipo}",
                    },
                }
            )
    return sorted(ofertas, key=lambda o: o["custo_total_c"])


def mudar_vencimento(ctx: Contexto) -> dict:
    """Return {"disponivel": bool, "dias": [int]}: available when tipo_falta == "PONTUAL";
    suggested days right after dia_recebimento_estimado.

    The two days following the receipt day, wrapping past 28 to day 1 (due days stay within 1..28).
    """
    if ctx.tipo_falta != "PONTUAL":
        return {"disponivel": False, "dias": [], "origem": {"disponivel": "core.ofertas"}}
    d = ctx.dia_recebimento_estimado
    primeiro = d + 1 if d < 28 else 1
    return {
        "disponivel": True,
        "dias": [primeiro, primeiro + 1 if primeiro < 28 else 1],
        "origem": {"disponivel": "core.ofertas", "dias": "core.ofertas"},
    }
