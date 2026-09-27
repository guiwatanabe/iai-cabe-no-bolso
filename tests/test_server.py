import json
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters

from mcp_server import dados, server
from mcp_server.core import fatura, taxas
from mcp_server.core.tipos import Contexto, EstadoSessao

ROOT = Path(__file__).resolve().parents[1]
ANA = "fixture-escorregao"
BRUNO = "fixture-rolando"
CLIENTES = [ANA, BRUNO, "fixture-no-limite"]
CONSENTE = EstadoSessao(consentimento=True, primeiro_nome="Ana")
PROIBIDOS = ["pedalada", "grupo", "faturas_abaixo", "ESCORREGAO", "ROLANDO_FATURA", "NO_LIMITE"]

# Fakes shaped exactly as the core docstrings.
COB = {
    "id": "cob_01",
    "tipo": "cobertura_cheque_especial",
    "custo_total_c": 1680,
    "taxa_mes": 0.08,
    "prazo_dias": 15,
    "parcela_c": None,
    "parcelas": None,
    "amplia_limite": False,
    "novo_limite_c": None,
    "origem": {"custo_total_c": "core.ofertas", "taxa_mes": "taxas.yaml:cobertura_curta"},
}
CONS = {
    "id": "cons_01",
    "tipo": "credito_consignado",
    "custo_total_c": 12000,
    "taxa_mes": 0.035,
    "prazo_dias": None,
    "parcela_c": 9000,
    "parcelas": 10,
    "amplia_limite": False,
    "novo_limite_c": None,
    "origem": {"custo_total_c": "core.ofertas", "taxa_mes": "taxas.yaml:credito_consignado"},
}


def fake_formas(ctx, tx):
    return {
        "valor_c": ctx.fatura_estimada_c,
        "dia_vencimento": ctx.dia_vencimento,
        "minimo_c": 25500,
        "encargos_se_pagar_minimo_c": 20230,
        "parcelamento_fatura": [
            {"parcelas": 3, "parcela_c": 68000, "custo_total_c": 34000, "taxa_mes": 0.094},
            {"parcelas": 6, "parcela_c": 38000, "custo_total_c": 58000, "taxa_mes": 0.094},
        ],
        "origem": {"valor_c": "gold_contexto_agente", "minimo_c": "core.fatura", "parcelamento_fatura": "core.fatura"},
    }


def fake_acompanhamento(ctx, estado):
    if not estado.oferta_aceita:
        return None
    return {"tipo": "cobertura", "status": "aguardando", "valor_c": 42000, "dia_recebimento": 5}


@pytest.fixture(autouse=True)
def fixtures(monkeypatch):
    monkeypatch.setenv("CABE_DADOS", "fixtures")
    monkeypatch.setattr(server.fatura, "formas_de_pagar", fake_formas)
    monkeypatch.setattr(server.ofertas, "ofertas_liberadas", lambda ctx, e, tx: [COB, CONS] if e.consentimento else [])
    monkeypatch.setattr(server.ofertas, "mudar_vencimento", lambda ctx: {"disponivel": True, "dias": [6, 7]})
    rot = {"custo_c": 5880, "limitado_pelo_teto": False, "origem": {"custo_c": "core.rotativo"}}
    monkeypatch.setattr(server.rotativo, "custo_rotativo", lambda restante_c, meses, tx: rot)
    monkeypatch.setattr(server.acompanhamento, "acompanhamento", fake_acompanhamento)
    dados.contexto.cache_clear()
    dados.transacoes.cache_clear()
    yield
    dados.contexto.cache_clear()
    dados.transacoes.cache_clear()


FATURA_KEYS = {
    "fatura_valor",
    "fatura_vencimento",
    "fatura_minimo",
    "fatura_encargos_se_pagar_minimo",
    "parcelamento_fatura_3x_parcela",
    "parcelamento_fatura_3x_custo_total",
    "parcelamento_fatura_6x_parcela",
    "parcelamento_fatura_6x_custo_total",
}


def test_without_consent_only_bill_facts():
    for estado in (None, EstadoSessao(primeiro_nome="Ana")):
        r = server.contexto_fatura(ANA, estado)
        assert r.status == "ok" and set(r.facts) == FATURA_KEYS
        assert set(r.contexto) == {"consentimento", "primeiro_nome", "formas_de_pagar"}
        assert r.contexto["consentimento"] is False
        assert r.contexto["formas_de_pagar"] == ["total", "parcelamento da fatura", "mínimo"]
        assert set(r.meta["origem"]) == set(r.facts)


def test_with_consent_units_and_scale():
    r = server.contexto_fatura(ANA, CONSENTE)
    facts = {k: (f.value, f.unit) for k, f in r.facts.items()}
    assert facts["fatura_valor"] == (1700.0, "BRL")
    assert facts["fatura_vencimento"] == (20, "dia")
    assert facts["parcelamento_fatura_3x_parcela"] == (680.0, "BRL")
    assert facts["falta_prevista"] == (420.0, "BRL")
    assert facts["proximo_recebimento_dia"] == (5, "dia")
    assert facts["dias_ate_recebimento"] == (15, "dias")
    assert facts["oferta_cob_01_custo_total"] == (16.8, "BRL")
    assert facts["oferta_cob_01_taxa"] == (8.0, "pct")
    assert facts["oferta_cob_01_prazo"] == (15, "dias")
    assert facts["oferta_cons_01_taxa"] == (3.5, "pct")
    assert facts["oferta_cons_01_parcela"] == (90.0, "BRL")
    assert facts["oferta_cons_01_parcelas"] == (10, "parcelas")
    assert facts["rotativo_custo_1_mes"] == (58.8, "BRL")
    assert facts["mudar_vencimento_dia_2"] == (7, "dia")
    assert "oferta_cob_01_parcela" not in facts and "oferta_cons_01_prazo" not in facts
    assert r.facts["oferta_cob_01_custo_total"].label == "custo total da oferta cob_01 (cobertura com cheque especial)"
    assert r.meta["origem"]["oferta_cons_01_taxa"] == "taxas.yaml:credito_consignado"
    assert set(r.meta["origem"]) == set(r.facts)
    assert r.contexto == {
        "consentimento": True,
        "primeiro_nome": "Ana",
        "publico_vulneravel": False,
        "tipo_de_falta": "pontual",
        "formas_de_pagar": ["total", "parcelamento da fatura", "mínimo"],
        "ofertas_liberadas": [
            {"id": "cob_01", "tipo": "cobertura_cheque_especial", "amplia_limite": False},
            {"id": "cons_01", "tipo": "credito_consignado", "amplia_limite": False},
        ],
        "mudar_vencimento": {"disponivel": True},
        "acompanhamento": None,
        "simulacao": server.SIMULACAO,
    }


def test_acompanhamento_numbers_become_facts():
    r = server.contexto_fatura(ANA, CONSENTE.model_copy(update={"oferta_aceita": {"id": "cob_01"}}))
    assert r.contexto["acompanhamento"] == {"tipo": "cobertura", "status": "aguardando"}
    assert r.facts["acompanhamento_valor_coberto"].value == 420.0
    assert r.facts["acompanhamento_dia_recebimento"].unit == "dia"


@pytest.mark.parametrize("cliente", CLIENTES)
def test_segmentation_never_exposed(cliente):
    for estado in (None, CONSENTE):
        dump = server.contexto_fatura(cliente, estado).model_dump_json()
        assert not [p for p in PROIBIDOS if p in dump]


def test_unknown_client_is_empty():
    assert server.contexto_fatura("nao-existe", CONSENTE).status == "empty"
    assert server.contexto_fatura().status == "empty"
    assert server.explicar_fatura("nao-existe").status == "empty"


def test_explicar_fatura_latest_month_by_category():
    r = server.explicar_fatura(BRUNO)
    assert r.status == "ok" and [row["valor_c"] for row in r.rows] == [52000, 42000, 31000, 28000]
    assert list(r.facts) == ["compras_lojas_e_sites", "compras_parcelas", "compras_restaurantes", "compras_mercado"]
    f = r.facts["compras_lojas_e_sites"]
    assert (f.label, f.value, f.unit) == ("compras em Lojas e sites em nov/2025", 520.0, "BRL")
    assert server.explicar_fatura(ANA).status == "empty"  # no transactions fixture


def test_bigquery_reads_are_parameterised(monkeypatch):
    monkeypatch.delenv("CABE_DADOS")
    calls = []
    row = json.loads((dados.FIXTURES / "gold_rolando.json").read_text())
    monkeypatch.setattr(dados.bq, "run", lambda sql, **p: calls.append((sql, p)) or [row])
    assert dados.contexto("x'; DROP") == Contexto.model_validate(row)
    dados.transacoes("x'; DROP")
    assert all(p == {"cliente_id": "x'; DROP"} and "DROP" not in sql for sql, p in calls)


def _core_pronto() -> bool:
    ctx = Contexto.model_validate_json((dados.FIXTURES / "gold_escorregao.json").read_text())
    try:
        fatura.formas_de_pagar(ctx, taxas.carregar())
    except NotImplementedError:
        return False
    return True


@pytest.mark.skipif(not _core_pronto(), reason="mcp_server.core not implemented yet (S2)")
def test_stdio_smoke():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_server.server"], env={"CABE_DADOS": "fixtures"}, cwd=ROOT
    )

    async def session():
        async with Client(params) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            estado = {"consentimento": True, "primeiro_nome": "Ana"}
            ctx = await client.call_tool("contexto_fatura", {"cliente_id": ANA, "estado": estado})
            exp = await client.call_tool("explicar_fatura", {"cliente_id": BRUNO})
            return tools, ctx, exp

    tools, ctx, exp = anyio.run(session)
    assert tools == {"contexto_fatura", "explicar_fatura"}
    assert not ctx.is_error and ctx.structured_content["status"] == "ok"
    assert ctx.structured_content["facts"]["fatura_valor"]["value"] == 1700.0
    assert ctx.structured_content["contexto"]["primeiro_nome"] == "Ana"
    assert exp.structured_content["facts"]["compras_lojas_e_sites"]["value"] == 520.0
