from types import SimpleNamespace

from google.adk.models import LlmResponse
from google.genai import types

from cabe.grounding import Answer, register_facts, render_answer

TOOL_RESPONSE = {
    "isError": False,
    "content": [{"type": "text", "text": "duplicate"}],
    "structuredContent": {
        "status": "ok",
        "facts": {
            "spent": {"label": "gasto", "value": 1234.5, "unit": "BRL"},
            "share": {"label": "fatia", "value": 12.34, "unit": "pct"},
            "count": {"label": "compras", "value": 1500},
        },
        "rows": [{"i": i} for i in range(60)],
        "meta": {},
    },
}


def model_says(status, text):
    part = types.Part(text=Answer(status=status, text=text).model_dump_json())
    return LlmResponse(content=types.Content(role="model", parts=[part]))


def rendered(ctx, status, text):
    return Answer.model_validate_json(render_answer(ctx, model_says(status, text)).content.parts[0].text)


def test_register_assigns_ids_and_strips_duplicates():
    ctx = SimpleNamespace(state={})
    out = register_facts(None, {}, ctx, TOOL_RESPONSE)
    assert list(out["facts"]) == ["f1", "f2", "f3"]
    assert "content" not in out and len(out["rows"]) == 50 and out["rows_truncated"]
    # ids keep growing across calls in the same session
    assert list(register_facts(None, {}, ctx, TOOL_RESPONSE)["facts"]) == ["f4", "f5", "f6"]


def test_render_substitutes_pt_br_values():
    ctx = SimpleNamespace(state={})
    register_facts(None, {}, ctx, TOOL_RESPONSE)
    answer = rendered(ctx, "answered", "Você gastou [[f1]] ([[f2]]) em [[f3]] compras.")
    assert answer.text == "Você gastou R$ 1.234,50 (12,3%) em 1.500 compras."


def test_unknown_id_rejects_whole_answer():
    ctx = SimpleNamespace(state={})
    register_facts(None, {}, ctx, TOOL_RESPONSE)
    answer = rendered(ctx, "answered", "Você gastou [[f1]] de [[f9]].")
    assert answer.status == "no_data" and "[[" not in answer.text and "R$" not in answer.text


def test_non_json_final_text_is_rejected():
    resp = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Você gastou R$ 999")]))
    answer = Answer.model_validate_json(render_answer(SimpleNamespace(state={}), resp).content.parts[0].text)
    assert answer.status == "no_data"


def test_tool_calls_and_partials_pass_through():
    call = LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="x"))]))
    assert render_answer(SimpleNamespace(state={}), call) is None
    partial = model_says("answered", "[[f1]]")
    partial.partial = True
    assert render_answer(SimpleNamespace(state={}), partial) is None
