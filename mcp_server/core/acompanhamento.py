"""Follow-up of an accepted offer (demo simulation). Pure; owner: S2."""

from .tipos import Contexto, EstadoSessao


def acompanhamento(ctx: Contexto, estado: EstadoSessao) -> dict | None:
    """None without estado.oferta_aceita. Cobertura: {"tipo": "cobertura", "status": "aguardando" | "coberta" |
    "atrasada", "valor_c", "dia_recebimento"}. Crédito: {"tipo": "credito", "parcelas_pagas", "parcelas",
    "proxima_parcela_c", "dia_proxima", "limite_liberado_c"}."""
    raise NotImplementedError
