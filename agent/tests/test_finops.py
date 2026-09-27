"""GenAI FinOps sem modelo: preço com fonte (config/finops.yaml), custo em USD por papel, projeção, travas, log por turno
e o bloco finops do painel nos modos sem_llm e llm (runtime falso)."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

os.environ["MODO_CONVERSA"] = "sem_llm"

from fastapi.testclient import TestClient  # noqa: E402

from cabe_core import finops  # noqa: E402
from cabe_no_bolso import callbacks  # noqa: E402
from server import finops as finops_srv  # noqa: E402
from server.main import criar_app  # noqa: E402
from server.sessoes import novo_estado, registrar_turno  # noqa: E402
from tests.conftest import ANA, BRUNO  # noqa: E402
from tests.test_api import RuntimeGi  # noqa: E402


# ------------------------------------------------------------------ cabe_core.finops (puro)
def test_preco_confirmado_com_fonte_e_vigencia():
    p = finops.preco_de("gemini-3.8-flash")
    assert p["confirmado"] and p["entrada_por_milhao_usd"] == 0.75 and p["saida_por_milhao_usd"] == 3.75
    assert "cloud.google.com" in p["fonte"] and "2026" in p["vigencia"]
    p2 = finops.preco_de("gemini-2.5-flash")
    assert p2["confirmado"] and p2["entrada_por_milhao_usd"] == 0.30 and p2["saida_por_milhao_usd"] == 2.50


def test_modelo_sem_preco_nao_inventa_custo():
    p = finops.preco_de("modelo-inexistente")
    assert p["confirmado"] is False and p["entrada_por_milhao_usd"] is None and "DESCONHECIDO" in p["fonte"]
    c = finops.custo_usd(1000, 100, "modelo-inexistente")
    assert c["custo_usd"] is None and c["preco_confirmado"] is False
    cfg = {"precos": {"x": {"entrada_por_milhao_usd": 1, "saida_por_milhao_usd": 2, "fonte": "f", "status": "conferir"}}}
    assert finops.preco_de("x", cfg)["confirmado"] is False   # status conferir => sem custo


def test_custo_usd_bate_com_o_baseline_do_yaml():
    # config/finops.yaml: sessão do Bruno = 15483/1e6*0.75 + 352/1e6*3.75 = US$ 0,0129
    c = finops.custo_usd(15483, 352, "gemini-3.8-flash")
    assert c["custo_usd"] == round(15483 / 1e6 * 0.75 + 352 / 1e6 * 3.75, 6) == 0.012932
    assert c["origem"] == "finops.custo_usd:gemini-3.8-flash" and c["preco_fonte"].startswith("https://")
    assert finops.custo_usd(0, 0, "gemini-3.8-flash")["custo_usd"] == 0.0


def test_custo_por_papel_separa_agente_e_validador():
    fin = {"chamadas_llm": 2, "chamadas_validador": 1, "tokens_entrada": 10_000, "tokens_saida": 300,
           "tokens_entrada_validador": 3_000, "tokens_saida_validador": 50}
    c = finops.custo_por_papel(fin, "gemini-3.8-flash", "gemini-2.5-flash")
    assert c["agente"]["tokens_entrada"] == 7_000 and c["agente"]["tokens_saida"] == 250 and c["agente"]["modelo"] == "gemini-3.8-flash"
    assert c["validador"]["tokens_entrada"] == 3_000 and c["validador"]["modelo"] == "gemini-2.5-flash" and c["validador"]["chamadas"] == 1
    assert c["agente"]["custo_usd"] == round(7_000 / 1e6 * 0.75 + 250 / 1e6 * 3.75, 6)
    assert c["validador"]["custo_usd"] == round(3_000 / 1e6 * 0.30 + 50 / 1e6 * 2.50, 6)
    assert c["total"]["custo_usd"] == round(c["agente"]["custo_usd"] + c["validador"]["custo_usd"], 6)
    # validador num modelo sem preço confirmado: total fica None (parcial), agente continua
    c2 = finops.custo_por_papel(fin, "gemini-3.8-flash", "sem-preco")
    assert c2["agente"]["custo_usd"] is not None and c2["validador"]["custo_usd"] is None and c2["total"]["custo_usd"] is None
    # sem tokens do validador, o modelo sem preço não contamina o total
    assert finops.custo_por_papel({"tokens_entrada": 100, "tokens_saida": 10}, "gemini-3.8-flash", "sem-preco")["total"]["custo_usd"] is not None


def test_projecao_e_so_multiplicacao_com_rotulo():
    pr = finops.projecao(0.0129)
    assert pr["rotulo"] == "projeção" and pr["clientes"] == 384 and pr["custo_usd"] == round(0.0129 * 384, 6)
    assert "docs/01" in pr["fonte_clientes"] and finops.projecao(None)["custo_usd"] is None


def test_travas_e_tetos_do_yaml(monkeypatch):
    t = finops.travas()
    assert t["chamadas_llm_por_sessao_max"] == 12 and t["tool_calls_por_turno_max"] == 8 and t["acompanhamento_com_llm"] is False
    assert t["orcamento_grupo_usd"] == 1000 and t["alertas_orcamento_pct"] == [50, 80, 100]
    monkeypatch.delenv("CHAMADAS_LLM_POR_SESSAO_MAX", raising=False)
    monkeypatch.delenv("TOOL_CALLS_POR_TURNO_MAX", raising=False)
    assert finops.teto_chamadas_por_sessao() == 12 and finops.teto_tool_calls_por_turno() == 8
    monkeypatch.setenv("TOOL_CALLS_POR_TURNO_MAX", "2")
    assert finops.teto_tool_calls_por_turno() == 2


# ------------------------------------------------------------------ before_tool: teto de ferramentas por turno (padrão do Guilherme)
def _ctx(**state):
    return SimpleNamespace(state=dict(state), session=SimpleNamespace(id="s1"), function_call_id="fc1", invocation_id="inv1")


def test_before_tool_bloqueia_acima_do_teto_por_turno(monkeypatch):
    monkeypatch.setenv("TOOL_CALLS_POR_TURNO_MAX", "3")
    ctx = _ctx(consentimento=True, cliente_id=BRUNO, anomes=202509)
    ferramenta = SimpleNamespace(name="simular_continuar_no_rotativo")
    for _ in range(3):
        assert callbacks.before_tool(ferramenta, {"meses": 1}, ctx) is None
    r = callbacks.before_tool(ferramenta, {"meses": 1}, ctx)
    assert r and r["bloqueado"] and "3" in r["motivo"] and "pessoa" in r["mensagem_cliente"]
    assert ctx.state[callbacks.CHAVE_TOOL_CALLS] == 4 and ctx.state["trace"][-1]["bloqueada"] and "teto" in ctx.state["trace"][-1]["resumo"]
    # novo turno: o ADK descarta o estado temp:, o contador volta a zero
    ctx2 = _ctx(consentimento=True, cliente_id=BRUNO, anomes=202509)
    assert callbacks.before_tool(ferramenta, {"meses": 1}, ctx2) is None


# ------------------------------------------------------------------ log por turno (Cloud Logging) e custo do turno
def test_registrar_turno_calcula_custo_e_escreve_log_json_sem_pii(monkeypatch, capsys):
    monkeypatch.setenv("MODELO", "gemini-3.8-flash")
    monkeypatch.setenv("MODELO_VALIDADOR", "gemini-2.5-flash")
    estado = novo_estado(ANA, 202508, None, "Ana", "CLT", "llm", "gi")
    estado["sessao_id"] = "sess-teste-123"
    turno = registrar_turno(estado, acao="ver_opcoes", modo="conversa", gatilho="fechamento", llm=True, modo_conversa="gi",
                            checagens={"ok": True}, validador={"aplicado": True, "aprovado": True, "violacoes": []}, regeneracoes=0, mensagem_segura=False,
                            chamadas_llm=1, chamadas_validador=1, tokens_entrada=12_000, tokens_saida=200, tokens_entrada_validador=3_000,
                            tokens_saida_validador=40, latencia_ms=6800.0, latencias_validador_ms=[2500], acao_agente="mostrar_oferta", oferta_id="cob_01",
                            numeros_citados=["R$ 2.955,00"])
    assert turno["moeda"] == "USD" and turno["modelo"] == "gemini-3.8-flash" and turno["modelo_validador"] == "gemini-2.5-flash"
    assert turno["custo_agente_usd"] == round(9_000 / 1e6 * 0.75 + 160 / 1e6 * 3.75, 6)
    assert turno["custo_validador_usd"] == round(3_000 / 1e6 * 0.30 + 40 / 1e6 * 2.50, 6)
    assert turno["custo_usd"] == round(turno["custo_agente_usd"] + turno["custo_validador_usd"], 6)
    linhas = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{") and '"evento": "turno"' in l]
    assert [l["papel"] for l in linhas] == ["agente", "validador"]
    ag, va = linhas
    assert ag["sessao_id"] == "sess-teste-123" and ag["modelo"] == "gemini-3.8-flash" and ag["tokens_entrada"] == 9_000 and ag["tokens_saida"] == 160
    assert ag["latencia_ms"] == 6800.0 and ag["custo_usd"] == turno["custo_agente_usd"] and ag["validador_aprovado"] is True and ag["regeneracoes"] == 0
    assert va["modelo"] == "gemini-2.5-flash" and va["tokens_entrada"] == 3_000 and va["latencia_ms"] == 2500 and va["custo_usd"] == turno["custo_validador_usd"]
    for l in linhas:   # sem PII nem conteúdo do extrato
        assert set(l) >= {"severity", "evento", "ts", "sessao_id", "ordem", "papel", "modelo", "chamadas", "tokens_entrada", "tokens_saida", "latencia_ms",
                          "custo_usd", "validador_aprovado", "regeneracoes", "mensagem_segura"}
        assert ANA not in json.dumps(l) and "texto" not in l and "numeros_citados" not in l and "R$" not in json.dumps(l)
    # turno em código: uma linha 'codigo' com custo zero
    registrar_turno(estado, acao="confirmar", modo="conversa", gatilho=None, llm=False, checagens={"ok": True}, validador={"aplicado": False},
                    regeneracoes=0, mensagem_segura=False, chamadas_llm=0, tokens_entrada=0, tokens_saida=0, latencia_ms=2.0)
    cod = [json.loads(l) for l in capsys.readouterr().out.splitlines() if '"evento": "turno"' in l]
    assert len(cod) == 1 and cod[0]["papel"] == "codigo" and cod[0]["custo_usd"] == 0.0 and estado["turnos"][-1]["custo_usd"] == 0.0


def test_log_turnos_desligado_por_env(monkeypatch, capsys):
    monkeypatch.setenv("LOG_TURNOS", "false")
    estado = novo_estado(ANA, 202508, None, "Ana", "CLT", "sem_llm", "sem_llm")
    registrar_turno(estado, acao="ver_opcoes", modo="conversa", gatilho=None, llm=False, checagens={}, validador={}, regeneracoes=0, mensagem_segura=False)
    assert not [l for l in capsys.readouterr().out.splitlines() if '"evento": "turno"' in l]
    assert finops_srv.logs_ligados() is False


# ------------------------------------------------------------------ painel: bloco finops no modo llm (runtime falso, sem modelo)
def test_painel_finops_modo_gi_custo_por_papel_projecao_e_teto(monkeypatch):
    from server import conversa

    falso = RuntimeGi()
    monkeypatch.setattr(conversa, "carregar_runtime", lambda: falso)
    monkeypatch.setenv("MODELO", "gemini-3.8-flash")
    monkeypatch.delenv("MODELO_VALIDADOR", raising=False)
    monkeypatch.setenv("CHAMADAS_LLM_POR_SESSAO_MAX", "3")
    monkeypatch.setenv("INSIGHT_COM_LLM", "true")
    with TestClient(criar_app(req_por_minuto=10_000)) as c:
        sid = c.post("/api/sessao", json={"cliente_id": ANA, "anomes": 202508}).json()["sessao_id"]
        c.post("/api/consentimento", json={"sessao_id": sid, "concedido": True})
        j = c.post("/api/mensagem", json={"sessao_id": sid, "acao": "ver_opcoes"}).json()
        assert j["turno"]["llm"] is True and j["turno"]["custo_usd"] is not None and j["turno"]["custo_usd"] > 0 and j["turno"]["moeda"] == "USD"
        p = c.get(f"/api/painel/{sid}").json()
        f = p["finops"]
        assert f["modo"] == "llm" and f["modelo"] == "gemini-3.8-flash" and f["moeda"] == "USD"
        assert f["chamadas_llm"] >= 1 and f["custo_estimado"] is not None and f["custo_estimado"] > 0
        assert f["custo_acumulado_sessao_usd"] == f["custo_estimado"]
        assert f["preco_entrada_por_milhao_usd"] == 0.75 and f["preco_saida_por_milhao_usd"] == 3.75 and f["preco_status"] == "confirmada"
        assert f["preco_fonte"].startswith("https://cloud.google.com/") and "2026" in f["vigencia"]
        assert f["por_papel"]["agente"]["papel"] == "agente" and f["por_papel"]["validador"]["papel"] == "validador"
        assert f["custo_agente_usd"] is not None and f["custo_validador_usd"] is not None
        assert abs(f["custo_agente_usd"] + f["custo_validador_usd"] - f["custo_estimado"]) < 1e-6
        assert f["projecao_piloto"]["rotulo"] == "projeção" and f["projecao_piloto"]["clientes"] == 384
        assert f["projecao_piloto"]["custo_usd"] == round(f["custo_estimado"] * 384, 6)
        assert f["teto_chamadas_por_sessao"] == 3 and "DESCONHECIDO" not in json.dumps(f)
        turnos_llm = [t for t in f["custo_por_turno"] if t["llm"]]
        assert turnos_llm and all(t["custo_usd"] is not None and t["custo_usd"] > 0 for t in turnos_llm)
        assert abs(sum(t["custo_usd"] or 0 for t in f["custo_por_turno"]) - f["custo_estimado"]) < 1e-6   # a soma dos turnos fecha com a sessão
        # teto de chamadas por sessão: mensagem segura sem chamar o modelo e o painel mostra o teto batido
        n = len(falso.chamadas)
        j2 = c.post("/api/mensagem", json={"sessao_id": sid, "acao": "consigo_pagar"}).json()
        assert len(falso.chamadas) == n and j2["mensagem_segura"] is True and j2["checagens"]["teto_chamadas"]["limite"] == 3
        assert j2["turno"]["custo_usd"] == 0.0 and "formas de pagar" in j2["mensagens"][0]["texto"]
        p2 = c.get(f"/api/painel/{sid}").json()["finops"]
        assert p2["chamadas_llm"] >= p2["teto_chamadas_por_sessao"] and p2["custo_estimado"] == f["custo_estimado"]


def test_painel_finops_sem_llm_custo_zero_e_fonte(monkeypatch):
    with TestClient(criar_app(req_por_minuto=10_000)) as c:
        sid = c.post("/api/sessao", json={"cliente_id": BRUNO, "anomes": 202509}).json()["sessao_id"]
        c.post("/api/consentimento", json={"sessao_id": sid, "concedido": True})
        c.post("/api/mensagem", json={"sessao_id": sid, "acao": "ver_opcoes"})
        f = c.get(f"/api/painel/{sid}").json()["finops"]
        assert f["modo"] == "sem_llm" and f["modelo"] is None and f["custo_estimado"] == 0 and f["chamadas_llm"] == 0
        assert f["projecao_piloto"]["custo_usd"] == 0 and f["preco_fonte"].startswith("modo sem LLM")
        assert all(t["custo_usd"] == 0 and not t["llm"] for t in f["custo_por_turno"]) and len(f["custo_por_turno"]) >= 1
