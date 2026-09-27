"""Offers already released and filtered: the only credit the agent may mention. Pure; owner: S2."""

from .tipos import Contexto, EstadoSessao


def ofertas_liberadas(ctx: Contexto, estado: EstadoSessao, taxas: dict) -> list[dict]:
    """Released offers sorted by custo_total_c ascending; [] when none applies.

    Each: {"id", "tipo": "cobertura_cheque_especial" | "credito_consignado" | "credito_prestamista",
           "custo_total_c", "taxa_mes", "prazo_dias" | None, "parcela_c" | None, "parcelas" | None,
           "amplia_limite": bool, "novo_limite_c" | None, "origem"}.

    Empty when: no consent; qtd_pedaladas_12m >= regras.pedaladas_sem_credito; reincidencia_pos_credito;
    confianca_recebimento != "ALTA"; tipo_falta == "SEM_FALTA".
    Cobertura: elegivel_cobertura_curta, liberacao.cobertura, cheque_especial_zerado,
    dias_ate_recebimento <= cobertura_curta.teto_dias, valor_recebimento_tipico_c >= valor_faltante_c.
    Crédito: elegivel_parcelamento, liberacao.<produto>, not credito_usado_12m, taxa_mes not null,
    parcela_c <= folga_mensal_c (PMT over the configured prazos; keep the cheapest fitting prazo per product).
    """
    raise NotImplementedError


def mudar_vencimento(ctx: Contexto) -> dict:
    """Return {"disponivel": bool, "dias": [int]}: available when tipo_falta == "PONTUAL";
    suggested days right after dia_recebimento_estimado."""
    raise NotImplementedError
