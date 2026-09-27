"""Fixtures: taxas de config/taxas.yaml e a fonte CSV (carregada uma vez por sessão de testes)."""
import copy
import json

import pytest

from cabe_core import config, dados

ANA = "755627ab-804b-4211-b0ea-f4ebacc58716"        # Escorregão, 202508
BRUNO = "3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b"      # Rolando a fatura, 202509 (gabarito em data/personas)
NO_LIMITE = "0a37f67b-77d5-4ab8-af28-2d88aaa252f1"  # 6+ roladas (docs/01 §8)


@pytest.fixture(scope="session")
def taxas() -> dict:
    return config.carregar_taxas()


@pytest.fixture
def taxas_mutaveis(taxas) -> dict:
    return copy.deepcopy(taxas)


@pytest.fixture(scope="session")
def fonte() -> dados.FonteCsv:
    return dados.FonteCsv()


@pytest.fixture(scope="session")
def gabarito_bruno() -> dict:
    with open(config.raiz() / "data" / "personas" / "3e7d20b2_grupo_b.json", encoding="utf-8") as f:
        return json.load(f)
