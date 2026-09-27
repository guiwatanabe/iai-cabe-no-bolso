"""Policy do agente: travas de crédito (puras, de cabe_core.travas) + registro de taxas e liberação simulada (config/taxas.yaml).

Tudo que decide elegibilidade, "cabe no mês", C -> humano e taxa vive aqui e em cabe_core, nunca na instrução do modelo.
"""
from __future__ import annotations

from functools import lru_cache

from cabe_core import config
from cabe_core.travas import (  # noqa: F401  (reexport: a policy é a mesma para tools, testes e API)
    cabe_no_mes,
    cobertura_disponivel,
    cobre_ate_recebimento,
    custo_rotativo,
    grupo_permite,
    juntar,
    liberado,
    mais_barato_que_rotativo,
    parcelamento_disponivel,
    perfil_permite,
    requer_confirmacao_humana,
    sem_reincidencia,
    taxa_disponivel,
)

TERMOS_FORA_DO_PLANO = ("seguro", "prestamista", "cashback", "pontos", "cartão novo", "cartao novo", "investimento", "capitalização")


@lru_cache(maxsize=1)
def registro_taxas() -> dict:
    """Lê config/taxas.yaml uma vez por processo. Única fonte de taxas: o modelo nunca estima taxa."""
    return config.carregar_taxas()


def produtos_disponiveis(taxas: dict | None = None) -> list[str]:
    """Produtos que existem para o agente: só os com taxa configurada. Sem taxa, o produto some."""
    return config.produtos_com_taxa(taxas or registro_taxas())


def liberacao_para(cliente_id: str, taxas: dict | None = None) -> dict[str, bool]:
    """Serviço de crédito simulado: {produto: liberado} por cliente (liberacao_simulada em config)."""
    t = taxas or registro_taxas()
    bloco = t.get("liberacao_simulada") or {}
    lista = (bloco.get("por_cliente") or {}).get(cliente_id, bloco.get("padrao") or [])
    return {p: (p in lista) for p in produtos_disponiveis(t)}


def persona_demo(cliente_id: str, taxas: dict | None = None) -> dict | None:
    for p in (taxas or registro_taxas()).get("personas_demo") or []:
        if p.get("id") == cliente_id:
            return dict(p)
    return None


def personas_demo(taxas: dict | None = None) -> list[dict]:
    return [dict(p) for p in (taxas or registro_taxas()).get("personas_demo") or []]


def cabe(parcela: int, folga: int) -> bool:
    return cabe_no_mes(parcela, folga)["permitido"]


def encaminhar_humano(motivo: str) -> dict:
    """Caminho humano sempre disponível (docs/05, princípio 7). Sem produto."""
    return {"encaminhar": True, "motivo": motivo, "produto": None, "origem": "policy.encaminhar_humano"}


def termos_bloqueados(texto: str) -> list[str]:
    """Lista negra para o guardião: produto fora do plano nunca aparece na resposta."""
    t = (texto or "").lower()
    return [termo for termo in TERMOS_FORA_DO_PLANO if termo in t]
