"""Follow-up of an accepted offer (demo simulation). Pure; owner: S2."""

from .tipos import Contexto, EstadoSessao


def acompanhamento(ctx: Contexto, estado: EstadoSessao) -> dict | None:
    """None without estado.oferta_aceita. Cobertura: {"tipo": "cobertura", "status": "aguardando" | "coberta" |
    "atrasada", "valor_c", "dia_recebimento"}. Crédito: {"tipo": "credito", "parcelas_pagas", "parcelas",
    "proxima_parcela_c", "dia_proxima", "limite_liberado_c"}.

    Pure read of oferta_aceita ({"id", "tipo", ...demo fields}). Cobertura: status defaults to "aguardando".
    Crédito: parcelas_pagas defaults to 0, dia_proxima to dia_recebimento_estimado, and the card limit
    freed is the bill the credit paid (fatura_estimada_c).
    """
    oferta = estado.oferta_aceita
    if not oferta:
        return None
    if oferta["tipo"] == "cobertura_cheque_especial":
        return {
            "tipo": "cobertura",
            "status": oferta.get("status", "aguardando"),
            "valor_c": ctx.valor_faltante_fatura_c,
            "dia_recebimento": ctx.dia_recebimento_estimado,
            "origem": {
                "status": "core.acompanhamento",
                "valor_c": "gold_contexto_agente",
                "dia_recebimento": "gold_contexto_agente",
            },
        }
    return {
        "tipo": "credito",
        "parcelas_pagas": oferta.get("parcelas_pagas", 0),
        "parcelas": oferta["parcelas"],
        "proxima_parcela_c": oferta["parcela_c"],
        "dia_proxima": oferta.get("dia_proxima", ctx.dia_recebimento_estimado),
        "limite_liberado_c": ctx.fatura_estimada_c,
        "origem": {
            "parcelas_pagas": "core.acompanhamento",
            "parcelas": "core.acompanhamento",
            "proxima_parcela_c": "core.acompanhamento",
            "dia_proxima": "core.acompanhamento",
            "limite_liberado_c": "gold_contexto_agente",
        },
    }
