"""Correções da verificação final de 27/09 (02h), sem modelo: agente e validador falsos sobre o runtime real.

1. Insight em código cabe em 160 caracteres (Bruno saía com 171 e reprovava a checagem 'tamanho').
2. Modo tools, turno em que a ferramenta confirmar_plano rodou: texto fixo em código, validador não chamado, card de
   confirmação mantido, ação abrir_resumo_contrato com o id da Gi da opção confirmada.
3. Contexto do validador no modo tools com plano confirmado mantém as ofertas do mês (manter_ofertas).
4. texto_correcao em texto corrido para o modo tools (sem pedir JSON).
5. Turno em código dentro de uma sessão gi/tools fica rotulado pela sessão no painel (não 'sem_llm').
"""
import asyncio

import pytest

from cabe_core import capacidade
from cabe_core import ofertas as ofertas_mod
from cabe_no_bolso import checagens, policy, runtime, tools, validador
from tests.conftest import ANA, BRUNO
from tests.test_validador_offline import APROVADO, Falso, _sessao


def test_insight_em_codigo_cabe_em_160_caracteres(fonte, taxas):
    for cid, anomes in ((BRUNO, 202509), (ANA, 202508)):
        m = capacidade.motor(fonte, cid, anomes, taxas)
        o = ofertas_mod.montar(m, m["grupo"], taxas, policy.liberacao_para(cid, taxas))
        ins = runtime._insight({"consentimento": True, "motor": m, "ofertas": o})
        assert len(ins["texto"]) <= checagens.INSIGHT_MAX_CARACTERES, (cid, len(ins["texto"]))
        assert ins["estado"] in ("falta_pontual", "falta_que_se_repete")
    assert len(runtime._insight({"consentimento": False, "motor": None, "numeros_validados": []})["texto"]) <= checagens.INSIGHT_MAX_CARACTERES


class FalsoQueConfirma(Falso):
    """Modelo falso que, no turno do 'confirmar', chama a ferramenta confirmar_plano como o agente com ferramentas faria."""

    async def modelo(self, runner, sessao, texto):
        self.recebidas.append(texto)
        s = self.saidas.pop(0)
        if s == "<confirmar_plano>":
            ctx = runtime._ctx(sessao)
            r = tools.confirmar_plano(0, tool_context=ctx)
            runtime._trace_add(ctx.state, "confirmar_plano", {"indice_opcao": 0}, "plano confirmado", [], 0, "ferramenta")
            await runtime._aplicar_delta(runner, sessao, runtime._delta(ctx))
            assert r.get("caminho") == "parcelamento" and ctx.state.get("plano")
            return ["Fica assim: 10 parcelas de R$ 110,51; até a próxima fatura cabem R$ 2.590,36 no cartão."]
        return [s]


def test_modo_tools_confirmar_plano_vira_texto_fixo_sem_validador(monkeypatch):
    monkeypatch.setenv("MODO_CONVERSA", "tools")
    monkeypatch.setenv("VALIDADOR_ATIVO", "true")
    f = FalsoQueConfirma(["Sua fatura fechou em R$ 3.619,95 e vence dia 20. Faltam R$ 919,08.\n\nQuer ver as opções?", "<confirmar_plano>"], [APROVADO])
    monkeypatch.setattr(runtime, "_rodar_modelo", f.modelo)
    monkeypatch.setattr(validador, "validar_async", f.validar)
    sid = _sessao(BRUNO, 202509)
    r1 = runtime.conversar(sid, BRUNO, 202509, "consigo_pagar")
    assert r1["validador"]["aprovado"] is True and len(f.validacoes) == 1
    r2 = runtime.conversar(sid, BRUNO, 202509, "confirmar")
    assert r2["mensagens"][0]["texto"] == runtime.TEXTO_PLANO_CONFIRMADO
    assert len(f.validacoes) == 1                                     # o validador não é chamado no turno do plano confirmado
    assert r2["validador"]["aprovado"] is None and "texto fixo" in r2["validador"]["motivo"]
    assert r2["acao"] == "abrir_resumo_contrato" and r2["oferta_id"] == "con_01"
    assert [c["tipo"] for c in r2["cards"]] == ["confirmacao"] and "confirmar_plano" in r2["finops"]["ferramentas"]
    assert r2["guardiao"]["removidos"] == []                          # o texto fixo não tem número
    assert any(t.get("ferramenta") == "validador" and (t.get("argumentos") or {}).get("aplicado") is False and "não aplicado" in t.get("resumo", "")
               for t in runtime.trace(sid))
    p = runtime.painel(sid)
    assert p["plano"]["parcela"] == 11051 and p["validador"]["chamadas"] == 1      # só o turno anterior chamou o validador


def test_contexto_do_validador_mantem_ofertas_com_plano_confirmado(monkeypatch):
    monkeypatch.setenv("MODO_CONVERSA", "tools")
    sid = _sessao(BRUNO, 202509)

    async def _rodar():
        runner = runtime._runner_da_sessao(sid, modo="tools")
        sessao = await runtime._obter_sessao(runner, sid, BRUNO)
        ctx = runtime._ctx(sessao)
        tools.analisar_fatura(tool_context=ctx)
        tools.listar_ofertas(tool_context=ctx)
        tools.confirmar_plano(0, tool_context=ctx)
        await runtime._aplicar_delta(runner, sessao, runtime._delta(ctx))
        sessao = await runtime._obter_sessao(runner, sid, BRUNO)
        sem = await runtime._montar_contexto_sessao(runner, sessao, "conversa", "pergunta_cliente")
        sessao = await runtime._obter_sessao(runner, sid, BRUNO)
        com = await runtime._montar_contexto_sessao(runner, sessao, "conversa", "pergunta_cliente", manter_ofertas=True)
        return sem, com

    sem, com = asyncio.run(_rodar())
    assert sem["contexto"]["ofertas_liberadas"] == [] and sem["contexto"]["acompanhamento"]["credito_ativo"]
    ids = [o["id"] for o in com["contexto"]["ofertas_liberadas"]]
    assert ids and ids[0] == "con_01" and com["ids"]["con_01"] == 0
    assert com["contexto"]["acompanhamento"]["credito_ativo"]           # o plano continua no contexto
    assert "R$ 186,02" in com["indice"] and com["indice"]["R$ 186,02"]["origem"].startswith("ofertas.montar")


def test_texto_correcao_em_texto_corrido_para_o_modo_tools():
    r = validador.interpretar({"aprovado": False, "violacoes": [{"regra": "R1", "trecho": "x", "motivo": "inventado"}], "orientacao_para_regenerar": "use o contexto"})
    em_json = validador.texto_correcao(r)
    em_texto = validador.texto_correcao(r, em_json=False)
    assert "formato JSON" in em_json and "numeros_citados" in em_json
    assert "JSON" not in em_texto.split("\n")[0].replace("sem JSON", "") and "texto corrido" in em_texto and "numeros_citados" not in em_texto
    assert "R1 (bloqueante): inventado" in em_texto and "use o contexto" in em_texto


@pytest.fixture
def cliente_sem_llm(monkeypatch):
    from fastapi.testclient import TestClient
    from server.main import criar_app
    monkeypatch.setenv("MODO_CONVERSA", "sem_llm")
    with TestClient(criar_app(req_por_minuto=10_000)) as c:
        yield c


def test_turno_em_codigo_rotulado_pela_sessao(cliente_sem_llm):
    c = cliente_sem_llm
    sid = c.post("/api/sessao", json={"cliente_id": BRUNO, "anomes": 202509, "persona": "bruno"}).json()["sessao_id"]
    r = c.post("/api/consentimento", json={"sessao_id": sid, "concedido": True}).json()
    assert r["turno"]["modo_conversa"] == "sem_llm"                  # sessão sem LLM: rótulo da própria sessão
    assert r["turno"]["checagens"]["tamanho"]["ok"] is True          # insight do Bruno em código cabe em 160
