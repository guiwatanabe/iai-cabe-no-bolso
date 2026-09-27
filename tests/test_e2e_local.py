"""End to end, locally: ADK Runner + callbacks + the real MCP server over stdio reading tests/fixtures.

Only the models are fake: a scripted LLM stands in for Gemini, and the validator's model call is patched.
The toolset is rebuilt with CABE_DADOS=fixtures, so nothing here can reach BigQuery.
"""

import json
import os
import sys

import anyio
import pytest
from google.adk.models import BaseLlm, LlmCapabilities, LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters
from pydantic import Field

from cabe import agent, validador

APROVA = json.dumps({"aprovado": True, "violacoes": []})


class ScriptedLlm(BaseLlm):
    """Each call pops the next step: a tool name to call, or a function(fatos) -> answer dict.

    `fatos` maps each fact label of the latest tool result to its [[fN]] id, as the real model would read it.
    """

    steps: list
    requests: list = Field(default_factory=list)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)  # like Gemini on Vertex: final answer as text

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request)
        step = self.steps.pop(0)
        if isinstance(step, str):
            part = types.Part(function_call=types.FunctionCall(name=step, args={}))
        else:
            part = types.Part(text=json.dumps(step(fatos(llm_request)), ensure_ascii=False))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def ultimo_resultado(llm_request) -> dict:
    for content in reversed(llm_request.contents):
        for part in content.parts or []:
            if part.function_response:
                return part.function_response.response
    return {}


def fatos(llm_request) -> dict:
    return {f["label"]: f"[[{fid}]]" for fid, f in (ultimo_resultado(llm_request).get("facts") or {}).items()}


def run(steps, state, verdicts=(), mensagem="Consigo pagar minha fatura?"):
    """One turn through the real Runner; returns (final answer dict, the fake model)."""
    model = ScriptedLlm(model="scripted", steps=list(steps))
    toolset = McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,
                args=["-m", "mcp_server.server"],
                cwd=agent.REPO_ROOT,
                env={**os.environ, "CABE_DADOS": "fixtures"},
            ),
            timeout=30,
        ),
        tool_filter=["contexto_fatura", "explicar_fatura"],
    )
    runner = InMemoryRunner(agent=agent.root_agent.clone(update={"model": model, "tools": [toolset]}), app_name="cabe")

    async def turn():
        try:
            session = await runner.session_service.create_session(app_name="cabe", user_id="u", state=state)
            msg = types.Content(role="user", parts=[types.Part(text=mensagem)])
            texts = [
                p.text
                async for e in runner.run_async(user_id="u", session_id=session.id, new_message=msg)
                if e.content and e.author == "cabe"
                for p in e.content.parts or []
                if p.text
            ]
            return json.loads(texts[-1])
        finally:
            await toolset.close()

    queue = list(verdicts)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(validador, "gerar_json_gemini", lambda prompt: queue.pop(0) if queue else APROVA)
        return anyio.run(turn), model


ANA = {
    "cliente_id": "fixture-escorregao",
    "estado": {"consentimento": True, "primeiro_nome": "Ana"},
    "modo": "conversa",
    "gatilho": "fechamento",
}


def ana_cobertura(f):
    """PRD example C1, citing only fact ids."""
    return {
        "mensagens": [
            (
                f"Oi, Ana. Sua fatura fechou em {f['valor da fatura atual']} e vence "
                f"{f['dia de vencimento da fatura atual']}. Até lá, a previsão é ter "
                f"{f['saldo previsto no vencimento da fatura (estimativa das entradas e saídas do ciclo, não é o saldo da conta)']} na conta."
            ),
            (
                "Uma opção é pagar a fatura inteira usando o cheque especial por "
                f"{f['prazo em dias da oferta cob_01 (cobertura com cheque especial)']}. O custo total fica em "
                f"{f['custo total da oferta cob_01 (cobertura com cheque especial)']}."
            ),
            "Quer ver os detalhes?",
        ],
        "acao": "mostrar_oferta",
        "oferta_id": "cob_01",
    }


def test_ana_gets_the_cobertura_rendered_from_real_tool_facts():
    out, model = run(["contexto_fatura", ana_cobertura], ANA)
    assert out == {
        "mensagens": [
            "Oi, Ana. Sua fatura fechou em R$ 1.700 e vence dia 20. Até lá, a previsão é ter R$ 1.280 na conta.",
            "Uma opção é pagar a fatura inteira usando o cheque especial por 15 dias. O custo total fica em R$ 16,80.",
            "Quer ver os detalhes?",
        ],
        "acao": "mostrar_oferta",
        "oferta_id": "cob_01",
        "numeros_citados": ["R$ 1.700", "dia 20", "R$ 1.280", "15 dias", "R$ 16,80"],
    }
    # the model never sees the segmentation, even inside the tool result
    tool_result = json.dumps(ultimo_resultado(model.requests[-1]), ensure_ascii=False)
    assert "pedalada" not in tool_result and "ESCORREGAO" not in tool_result


def test_without_consent_the_tool_only_returns_the_bill_and_an_offer_becomes_the_safe_message():
    state = {**ANA, "estado": {"consentimento": False, "primeiro_nome": "Ana"}}

    def tenta_oferta(f):
        assert "custo total da oferta cob_01 (cobertura com cheque especial)" not in f
        assert (
            "saldo previsto no vencimento da fatura (estimativa das entradas e saídas do ciclo, não é o saldo da conta)"
            not in f
        )
        return {"mensagens": ["Veja a oferta."], "acao": "mostrar_oferta", "oferta_id": "cob_01"}

    out, _ = run(["contexto_fatura", tenta_oferta], state)
    assert out["acao"] == "mostrar_formas_de_pagar" and out["oferta_id"] is None
    assert out["mensagens"][0] == "Sua fatura fechou em R$ 1.700 e vence dia 20. Veja as formas de pagar."


def test_an_answer_without_the_tool_forces_the_call_through_the_real_loop():
    def responde_sem_dados(f):
        return {"mensagens": ["Sua fatura cabe."], "acao": "nenhuma", "oferta_id": None}

    out, model = run([responde_sem_dados, ana_cobertura], ANA)
    assert out["oferta_id"] == "cob_01" and "R$ 1.700" in out["mensagens"][0]
    assert len(model.requests) == 2 and ultimo_resultado(model.requests[1])["contexto"]["tipo_de_falta"] == "pontual"


def test_a_validator_rejection_is_regenerated_with_its_guidance():
    violacao = {"regra": "R6", "gravidade": "bloqueante", "trecho": "vai dar certo", "motivo": "promessa"}
    reprova = json.dumps({"aprovado": False, "violacoes": [violacao], "orientacao_para_regenerar": "Não prometa."})

    def promete(f):
        return {"mensagens": [f"Vai dar certo, {f['valor da fatura atual']}."], "acao": "nenhuma", "oferta_id": None}

    out, model = run(["contexto_fatura", promete, ana_cobertura], ANA, verdicts=[reprova, APROVA])
    assert out["oferta_id"] == "cob_01"
    # the regeneration went through a second contexto_fatura call carrying the guidance
    assert "Não prometa." in ultimo_resultado(model.requests[-1])["orientacao_para_regenerar"]


def test_bruno_gets_the_consignado_and_no_prestamista():
    state = {**ANA, "cliente_id": "fixture-rolando", "estado": {"consentimento": True, "primeiro_nome": "Bruno"}}

    def bruno(f):
        return {
            "mensagens": [
                (
                    "Um crédito com consignado paga a fatura e é devolvido em "
                    f"{f['número de parcelas da oferta cons_01 (crédito consignado)']} parcelas de "
                    f"{f['valor de cada parcela da oferta cons_01 (crédito consignado)']}."
                )
            ],
            "acao": "mostrar_oferta",
            "oferta_id": "cons_01",
        }

    out, model = run(["contexto_fatura", bruno], state)
    assert out["mensagens"] == ["Um crédito com consignado paga a fatura e é devolvido em 10 parcelas de R$ 90,18."]
    ofertas = ultimo_resultado(model.requests[-1])["contexto"]["ofertas_liberadas"]
    assert [o["id"] for o in ofertas] == ["cons_01"]
