from types import SimpleNamespace

import pytest

from cabe.grounding import Resposta, format_fact, register_facts, render_answer

CONTEXTO = {"consentimento": True, "primeiro_nome": "Ana", "ofertas_liberadas": [{"id": "cob_01"}]}
TOOL_RESPONSE = {
    "isError": False,
    "content": [{"type": "text", "text": "duplicate"}],
    "structuredContent": {
        "status": "ok",
        "facts": {
            "fatura_valor": {"label": "valor da fatura", "value": 1234.5, "unit": "BRL"},
            "cob_01_taxa": {"label": "taxa", "value": 12.34, "unit": "pct"},
            "fatura_vencimento": {"label": "vencimento", "value": 20, "unit": "dia"},
        },
        "contexto": CONTEXTO,
        "rows": [{"i": i} for i in range(60)],
        "meta": {},
    },
}
CONTEXTO_TOOL = SimpleNamespace(name="contexto_fatura")


def test_register_assigns_ids_and_strips_duplicates():
    ctx = SimpleNamespace(state={})
    out = register_facts(CONTEXTO_TOOL, {}, ctx, TOOL_RESPONSE)
    assert list(out["facts"]) == ["f1", "f2", "f3"]
    assert "content" not in out and len(out["rows"]) == 50 and out["rows_truncated"]
    assert out["contexto"] == CONTEXTO  # passed through to the model
    # ids keep growing across calls in the same session
    assert list(register_facts(CONTEXTO_TOOL, {}, ctx, TOOL_RESPONSE)["facts"]) == ["f4", "f5", "f6"]


def test_register_keeps_latest_contexto_only_for_contexto_fatura():
    ctx = SimpleNamespace(state={})
    register_facts(SimpleNamespace(name="explicar_fatura"), {}, ctx, TOOL_RESPONSE)
    assert "contexto" not in ctx.state and "temp:contexto_ok" not in ctx.state
    register_facts(CONTEXTO_TOOL, {}, ctx, TOOL_RESPONSE)
    assert ctx.state["contexto"] == CONTEXTO
    assert ctx.state["fatos_contexto"] == {"fatura_valor": "f4", "cob_01_taxa": "f5", "fatura_vencimento": "f6"}
    assert ctx.state["temp:contexto_ok"]


def test_register_ignores_tool_errors():
    ctx = SimpleNamespace(state={})
    assert register_facts(CONTEXTO_TOOL, {}, ctx, {"isError": True}) is None
    assert ctx.state == {}


def test_render_substitutes_every_text_field_and_cites_numbers():
    facts = register_facts(CONTEXTO_TOOL, {}, SimpleNamespace(state={}), TOOL_RESPONSE)["facts"]
    conversa = Resposta(mensagens=["Fatura de [[f1]], vence [[f3]].", "De novo [[f1]] a [[f2]]."], acao="nenhuma")
    out = render_answer(conversa, facts)
    assert out["mensagens"] == ["Fatura de R$ 1.234,50, vence dia 20.", "De novo R$ 1.234,50 a 12,34%."]
    assert out["numeros_citados"] == ["R$ 1.234,50", "dia 20", "12,34%"]  # order of appearance, deduplicated

    insight = Resposta(texto="Vence [[f3]].", botao_primario={"rotulo": "Pagar [[f1]]", "acao": "abrir_chat"})
    out = render_answer(insight, facts)
    assert out["texto"] == "Vence dia 20." and out["botao_primario"]["rotulo"] == "Pagar R$ 1.234,50"
    assert out["numeros_citados"] == ["dia 20", "R$ 1.234,50"]


def test_unknown_id_rejects_whole_answer():
    facts = {"f1": {"value": 1, "unit": "BRL"}}
    assert render_answer(Resposta(mensagens=["[[f1]] de [[f9]]"], acao="nenhuma"), facts) is None
    insight = Resposta(texto="ok", botao_primario={"rotulo": "[[f7]]", "acao": "nenhuma"})
    assert render_answer(insight, facts) is None


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (1700, "BRL", "R$ 1.700"),
        (1700.0, "BRL", "R$ 1.700"),
        (1141.83, "BRL", "R$ 1.141,83"),
        (38.5, "BRL", "R$ 38,50"),
        (3.5, "pct", "3,5%"),
        (8, "pct", "8%"),
        (20, "dia", "dia 20"),
        (15, "dias", "15 dias"),
        (1, "dias", "1 dia"),
        (12, "parcelas", "12"),
        (1500, None, "1.500"),
        ("cob_01", None, "cob_01"),
    ],
)
def test_format_fact_units(value, unit, expected):
    assert format_fact({"value": value, "unit": unit}) == expected
