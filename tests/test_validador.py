import json
import logging
from pathlib import Path

from google.adk.evaluation.eval_config import EvalConfig, get_eval_metrics_from_config
from google.adk.evaluation.eval_set import EvalSet

from cabe.validador import com_validacao, montar_prompt, validar

EVAL_DIR = Path(__file__).resolve().parents[1] / "agents" / "cabe" / "eval"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

CONTEXTO = {
    "gatilho": "fechamento",
    "consentimento": True,
    "fatura": {"valor": "R$ 1.700", "vencimento": "dia 20"},
    "ofertas_liberadas": [{"id": "cob_01", "tipo": "cobertura_cheque_especial"}],
}
RESPOSTA = {"mensagens": ["Oi, Ana."], "acao": "nenhuma", "oferta_id": None}
APROVA = json.dumps({"aprovado": True, "violacoes": [], "orientacao_para_regenerar": None})


def reprova(regra="R6", orientacao="Não prometa."):
    violacao = {"regra": regra, "gravidade": "bloqueante", "trecho": "vai dar certo", "motivo": "promessa"}
    return json.dumps({"aprovado": False, "violacoes": [violacao], "orientacao_para_regenerar": orientacao})


def roteiro(*saidas):
    """Fake validator model: returns the scripted outputs in order and records the prompts."""
    prompts, fila = [], list(saidas)

    def gerar_json(prompt):
        prompts.append(prompt)
        return fila.pop(0)

    return gerar_json, prompts


def agente():
    """Fake agent: records the orientation it got on each call."""
    chamadas = []

    def gerar(orientacao):
        chamadas.append(orientacao)
        return {**RESPOSTA, "tentativa": len(chamadas)}

    return gerar, chamadas


def test_prompt_has_rules_and_inputs():
    prompt = montar_prompt(CONTEXTO, [{"cliente": "Ver opções"}], RESPOSTA)
    assert all(f"R{i}." in prompt for i in range(1, 21))
    assert "R$ 1.700" in prompt and "Ver opções" in prompt and '"acao": "nenhuma"' in prompt
    assert "{{" not in prompt


def test_validar_approves():
    assert validar(CONTEXTO, [], RESPOSTA, lambda _: APROVA).aprovado


def test_validar_rejects_with_violations():
    v = validar(CONTEXTO, [], RESPOSTA, lambda _: reprova())
    assert not v.aprovado and v.violacoes[0].regra == "R6"


def test_validar_invalid_output_rejects():
    for saida in ["não é json", '{"aprovado": "talvez"}', '{"violacoes": []}']:
        assert not validar(CONTEXTO, [], RESPOSTA, lambda _, s=saida: s).aprovado


def test_validar_call_failure_rejects():
    def falha(_):
        raise RuntimeError("timeout")

    assert not validar(CONTEXTO, [], RESPOSTA, falha).aprovado


def test_validar_approved_with_violations_is_rejected():
    saida = json.loads(reprova())
    saida["aprovado"] = True
    assert not validar(CONTEXTO, [], RESPOSTA, lambda _: json.dumps(saida)).aprovado


def test_com_validacao_approved_first_time():
    gerar, chamadas = agente()
    gerar_json, prompts = roteiro(APROVA)
    assert com_validacao(gerar, CONTEXTO, [], "conversa", gerar_json=gerar_json)["tentativa"] == 1
    assert chamadas == [None]
    assert '"modo": "conversa"' in prompts[0]


def test_com_validacao_approved_after_retry(caplog):
    gerar, chamadas = agente()
    gerar_json, _ = roteiro(reprova(), APROVA)
    with caplog.at_level(logging.WARNING, logger="cabe.validador"):
        out = com_validacao(gerar, CONTEXTO, [], "conversa", gerar_json=gerar_json)
    assert out["tentativa"] == 2
    assert chamadas[0] is None and "Não prometa." in chamadas[1] and "R6" in chamadas[1]
    assert "R6" in caplog.text and "vai dar certo" not in caplog.text  # rule ids only, no customer text


def test_com_validacao_rejected_twice_conversa():
    gerar, chamadas = agente()
    gerar_json, _ = roteiro(reprova(), reprova("R4"))
    out = com_validacao(gerar, CONTEXTO, [], "conversa", gerar_json=gerar_json)
    assert len(chamadas) == 2
    assert out["mensagens"][0] == "Sua fatura fechou em R$ 1.700 e vence dia 20. Veja as formas de pagar."
    assert "alguém da equipe" in out["mensagens"][1]
    assert out["acao"] == "mostrar_formas_de_pagar" and out["oferta_id"] is None
    assert out["numeros_citados"] == ["R$ 1.700", "dia 20"]


def test_com_validacao_rejected_twice_insight():
    gerar, _ = agente()
    gerar_json, _ = roteiro("lixo", reprova())
    out = com_validacao(gerar, CONTEXTO, [], "insight", gerar_json=gerar_json)
    assert out["texto"] == "Sua fatura fechou em R$ 1.700 e vence dia 20. Veja as formas de pagar."
    assert out["botao_primario"]["acao"] == "ver_formas_de_pagar" and out["botao_secundario"] is None


def test_safe_message_without_bill_values_has_no_numbers():
    gerar, _ = agente()
    gerar_json, _ = roteiro(reprova(), reprova())
    out = com_validacao(gerar, {"consentimento": False}, [], "insight", gerar_json=gerar_json)
    assert out["texto"] == "Sua fatura fechou. Veja as formas de pagar." and out["numeros_citados"] == []


def test_com_validacao_r8_goes_to_human_without_retry():
    gerar, chamadas = agente()
    gerar_json, prompts = roteiro(reprova("R8"))
    out = com_validacao(gerar, CONTEXTO, [{"cliente": "Estou devendo em todo lugar"}], "insight", gerar_json=gerar_json)
    assert len(chamadas) == 1 and len(prompts) == 1
    assert out["acao"] == "transferir_humano" and out["oferta_id"] is None
    assert out["mensagens"][0].startswith("Sua fatura fechou em R$ 1.700")


def test_eval_set_loads_with_adk_models():
    es = EvalSet.model_validate_json((EVAL_DIR / "cabe.evalset.json").read_text(encoding="utf-8"))
    clientes = {json.loads(p.read_text())["id_usuario"] for p in FIXTURES.glob("gold_*.json")}
    assert len(es.eval_cases) == 20
    assert len({c.eval_id for c in es.eval_cases}) == len(es.eval_cases)
    for c in es.eval_cases:
        state = c.session_input.state
        assert state["cliente_id"] in clientes and state["modo"] in {"insight", "conversa"}
        assert len(c.esperado) == len(c.conversation)
        for inv, esp in zip(c.conversation, c.esperado):
            assert inv.intermediate_data.tool_uses[0].name == "contexto_fatura"
            assert inv.rubrics and {"acao", "oferta_id", "deve_conter", "nao_deve_conter"} <= esp.keys()
            assert esp["oferta_id"] in {None, "cob_01", "cons_01"}


def test_eval_config_loads_with_adk_models():
    config = EvalConfig.model_validate_json((EVAL_DIR / "test_config.json").read_text())
    names = {m.metric_name for m in get_eval_metrics_from_config(config)}
    assert {"tool_trajectory_avg_score", "rubric_based_final_response_quality_v1"} <= names
