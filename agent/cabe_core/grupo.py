"""Grupo do cliente pelas faturas roladas (mínimo ou parcial) nos últimos 12 meses. Flag calculada em código, fora do prompt.

em_dia (0) · escorregao (1-2) · rolando (3-5) · no_limite (6+).
A escolha do mês atual (pagar o mínimo/parcial) conta como rolada quando informada em modo_atual:
a primeira pedalada de quem estava em dia é um Escorregão, não "em dia".
"""
from __future__ import annotations

from .dinheiro import mapa_origem

FAIXAS_PADRAO = {"em_dia": [0, 0], "escorregao": [1, 2], "rolando": [3, 5], "no_limite": [6, 99]}
ROTULOS_PADRAO = {"em_dia": "Em dia", "escorregao": "Escorregão", "rolando": "Rolando a fatura", "no_limite": "No limite"}
ORDEM = ("em_dia", "escorregao", "rolando", "no_limite")


def rolada(modo: str | None) -> bool:
    return modo in ("minimo", "parcial")


def _sequencias(modos: list[str]) -> tuple[int, int]:
    """(maior sequência de roladas, roladas seguidas no fim da lista)."""
    maior = atual = 0
    for m in modos:
        atual = atual + 1 if rolada(m) else 0
        maior = max(maior, atual)
    return maior, atual


def classificar(faturas_anteriores: list[dict], modo_atual: str | None = None, faixas: dict | None = None,
                rotulos: dict | None = None) -> dict:
    """faturas_anteriores: faturas ANTES do mês atual (até 12), cada uma com 'modo' (e 'anomes').

    Devolve {grupo, rotulo, roladas_12m (só anteriores), roladas_com_atual, roladas_seguidas, maior_sequencia,
    meses_considerados, modo_atual, origem}.
    """
    faixas = faixas or FAIXAS_PADRAO
    rotulos = rotulos or ROTULOS_PADRAO
    ult = sorted(faturas_anteriores, key=lambda f: f.get("anomes", 0))[-12:]
    modos = [f["modo"] for f in ult]
    roladas_12m = sum(1 for m in modos if rolada(m))
    com_atual = roladas_12m + (1 if rolada(modo_atual) else 0)
    maior, seguidas = _sequencias(modos + ([modo_atual] if modo_atual else []))
    grupo = "no_limite"
    for nome in ORDEM:
        lo, hi = faixas[nome]
        if lo <= com_atual <= hi:
            grupo = nome
            break
    out = {
        "grupo": grupo,
        "rotulo": rotulos.get(grupo, grupo),
        "roladas_12m": roladas_12m,
        "roladas_com_atual": com_atual,
        "roladas_seguidas": seguidas,
        "maior_sequencia": maior,
        "meses_considerados": len(modos),
        "modo_atual": modo_atual,
    }
    out["origem"] = mapa_origem(out, "grupo.classificar")
    return out
