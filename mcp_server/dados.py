"""Client data: one cached parameterised read per client (+ its transactions).

`CABE_DADOS=fixtures` reads `tests/fixtures/` instead of BigQuery (local runs and tests).
"""

import json
import os
from functools import cache, lru_cache
from pathlib import Path

from mcp_server import bq
from mcp_server.core.tipos import Contexto

GOLD = "hackathon_dados.gold_contexto_agente"
# gold renamed two columns (sql/gold/03_contexto_agente.sql); Contexto keeps the contract names
_GOLD_COLS = {
    "saldo_previsto_vencimento_c": "caixa_disponivel_estimado_c",
    "valor_faltante_c": "valor_faltante_fatura_c",
}
TRANSACOES = "hackathon_dados.silver_transacoes_resumo"
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _fixtures() -> bool:
    return os.getenv("CABE_DADOS") == "fixtures"


def _tabela(nome: str) -> str:
    return f"`{os.getenv('GOOGLE_CLOUD_PROJECT')}.{nome}`"


@cache
def _gold_fixtures() -> dict[str, tuple[str, dict]]:
    """{id_usuario: (nome, row)} from `gold_<nome>.json`."""
    rows = {}
    for path in FIXTURES.glob("gold_*.json"):
        row = json.loads(path.read_text())
        rows[row["id_usuario"]] = (path.stem.removeprefix("gold_"), row)
    return rows


@lru_cache(maxsize=256)
def contexto(cliente_id: str) -> Contexto | None:
    if _fixtures():
        found = _gold_fixtures().get(cliente_id)
        return Contexto.model_validate(found[1]) if found else None
    cols = ", ".join(f"{_GOLD_COLS[c]} AS {c}" if c in _GOLD_COLS else c for c in Contexto.model_fields)
    rows = bq.run(f"SELECT {cols} FROM {_tabela(GOLD)} WHERE id_usuario = @cliente_id LIMIT 1", cliente_id=cliente_id)
    return Contexto.model_validate(rows[0]) if rows else None


@lru_cache(maxsize=256)
def transacoes(cliente_id: str) -> list[dict]:
    """Categories of the latest month, by valor_c descending: [{"anomes", "categoria", "valor_c"}]."""
    if _fixtures():
        found = _gold_fixtures().get(cliente_id)
        path = FIXTURES / f"transacoes_{found[0]}.json" if found else None
        if not path or not path.exists():
            return []
        rows = json.loads(path.read_text())
        ultimo = max(r["anomes"] for r in rows)
        rows = [{k: r[k] for k in ("anomes", "categoria", "valor_c")} for r in rows if r["anomes"] == ultimo]
        return sorted(rows, key=lambda r: r["valor_c"], reverse=True)
    return bq.run(
        f"""
        SELECT anomes, categoria, valor_c
        FROM {_tabela(TRANSACOES)}
        WHERE id_usuario = @cliente_id
        QUALIFY anomes = MAX(anomes) OVER ()
        ORDER BY valor_c DESC
        """,
        cliente_id=cliente_id,
    )
