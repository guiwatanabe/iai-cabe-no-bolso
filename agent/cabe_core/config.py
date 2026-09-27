"""Caminhos e leitura de config/taxas.yaml (única fonte de taxas e parâmetros). Lê uma vez por processo."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml


def raiz() -> Path:
    """Raiz do repositório: env RAIZ, senão dois níveis acima de agent/cabe_core/."""
    env = os.environ.get("RAIZ")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def caminho_taxas() -> Path:
    env = os.environ.get("TAXAS")
    if env:
        return Path(env).expanduser().resolve()
    return raiz() / "config" / "taxas.yaml"


def caminho_csv() -> Path:
    env = os.environ.get("DADOS_CSV")
    if env:
        return Path(env).expanduser().resolve()
    return raiz() / "data" / "extrato_sintetico.csv.gz"


@lru_cache(maxsize=8)
def _carregar(caminho: str) -> dict:
    with open(caminho, encoding="utf-8") as f:
        dados = yaml.safe_load(f) or {}
    if "rotativo" not in dados or "taxa_mes" not in dados["rotativo"]:
        raise ValueError(f"{caminho}: falta rotativo.taxa_mes (taxa da base)")
    return dados


def carregar_taxas(caminho: str | os.PathLike | None = None) -> dict:
    """Dict do YAML. Cacheado por caminho; o mesmo objeto é devolvido (não mutar)."""
    return _carregar(str(caminho or caminho_taxas()))


def produtos_com_taxa(taxas: dict) -> list[str]:
    """Produtos de crédito que existem para o agente: só os que têm taxa configurada."""
    out = []
    for nome in ("cheque_especial", "parcelamento_fatura", "credito_pessoal", "consignado_clt", "consignado_inss"):
        bloco = taxas.get(nome)
        if isinstance(bloco, dict) and (bloco.get("taxa_mes") or bloco.get("taxa_mes_base")):
            out.append(nome)
    return out
