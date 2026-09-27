"""Fluxo do modo gi e do validador sem modelo: agente e validador falsos (monkeypatch) sobre o runtime real.

Cobre: contexto -> agente -> checagens -> validador -> regeneração (1x) -> mensagem segura; R8 -> humano; ações viram
ferramentas de cabe_core (abrir_resumo_contrato -> confirmar_plano); validador no modo tools; validador indisponível.
"""
import json

import pytest

from cabe_no_bolso import checagens, runtime, validador
from tests.conftest import ANA, BRUNO

APROVADO = {"aprovado": True, "violacoes": [], "orientacao": None, "bloqueante": False, "r8": False, "corrigivel": False, "json_valido": True,
            "erro": None, "latencia_ms": 3, "tokens_entrada": 10, "tokens_saida": 2, "bruto": "", "modelo": "falso"}


def _reprovado(regra: str, motivo: str = "motivo", orientacao: str = "corrija"):
    r = validador.interpretar({"aprovado": False, "violacoes": [{"regra": regra, "trecho": "x", "motivo": motivo}], "orientacao_para_regenerar": orientacao})
    return {**r, "erro": None, "latencia_ms": 3, "tokens_entrada": 10, "tokens_saida": 2, "bruto": "", "modelo": "falso"}


class Falso:
    """Modelo falso: devolve as saídas na ordem; guarda as mensagens recebidas. Validador falso: vereditos na ordem."""

    def __init__(self, saidas, vereditos=None):
        self.saidas = list(saidas)
        self.vereditos = list(vereditos or [])
        self.recebidas = []
        self.validacoes = []

    async def modelo(self, runner, sessao, texto):
        self.recebidas.append(texto)
        s = self.saidas.pop(0)
        return [s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)]

    async def validar(self, saida, contexto, historico=None, *, modelo=None, diretrizes=None):
        self.validacoes.append({"saida": saida, "historico": historico})
        return self.vereditos.pop(0) if self.vereditos else APROVADO


@pytest.fixture
def gi(monkeypatch):
    monkeypatch.setenv("MODO_CONVERSA", "gi")
    monkeypatch.setenv("VALIDADOR_ATIVO", "true")

    def _armar(saidas, vereditos=None):
        f = Falso(saidas, vereditos)
        monkeypatch.setattr(runtime, "_rodar_modelo", f.modelo)
        monkeypatch.setattr(validador, "validar_async", f.validar)
        return f
    return _armar


def _sessao(cid, anomes, consentir=True):
    s = runtime.criar_sessao(cid, anomes)
    if consentir:
        runtime.consentir(s["sessao_id"], True)
    return s["sessao_id"]


PIX = {"mensagens": ["Oi, Ana. Sua fatura fechou em R$ 2.955,00 e vence dia 30. Vi um PIX de R$ 1.994,62 que entra todo mês: ele é renda certa?"],
       "acao": "nenhuma", "oferta_id": None, "numeros_citados": ["R$ 2.955,00", "dia 30", "R$ 1.994,62"]}
OFERTA = {"mensagens": ["Pela previsão, até o dia 30 faltam R$ 634,58. Uma opção é pagar tudo usando o cheque especial por 7 dias, até o salário do dia 7. O custo total fica em R$ 1,92.",
                        "Quer ver os detalhes?"], "acao": "mostrar_oferta", "oferta_id": "cob_01", "numeros_citados": ["dia 30", "R$ 634,58", "7 dias", "dia 7", "R$ 1,92"]}
ACEITE = {"mensagens": ["Combinado. Vou abrir o resumo para você conferir antes de confirmar."], "acao": "abrir_resumo_contrato", "oferta_id": "cob_01", "numeros_citados": []}
OFERTA_BRUNO = {"mensagens": ["Oi, Bruno. Sua fatura fechou em R$ 3.619,95 e vence dia 20. Pelo seu mês, faltam R$ 919,08.",
                              "Tenho uma ideia que pode aliviar: um crédito com consignado, devolvido em 10 parcelas de R$ 110,51. O custo total fica em R$ 186,02.",
                              "Quer ver os detalhes?"], "acao": "mostrar_oferta", "oferta_id": "con_01",
                "numeros_citados": ["R$ 3.619,95", "dia 20", "R$ 919,08", "10", "R$ 110,51", "R$ 186,02"]}


def test_jornada_ana_gi_pix_oferta_aceite(gi):
    f = gi([PIX, OFERTA, ACEITE])
    sid = _sessao(ANA, 202508)
    r1 = runtime.conversar(sid, ANA, 202508, "ver_opcoes")
    assert r1["modo_conversa"] == "gi" and r1["gatilho"] == "fechamento" and r1["acao"] == "nenhuma" and r1["validador"]["aprovado"] is True
    assert [c["acao"] for c in r1["sugestoes"]][:2] == ["confirmar_entrada_regular", "nao_contar_pix"]   # pergunta do PIX antes de qualquer oferta
    assert r1["mensagens"][0]["texto"].startswith("Oi, Ana") and r1["guardiao"]["removidos"] == []
    assert r1["finops"]["chamadas_validador"] == 1 and r1["finops"]["modo_conversa"] == "gi"
    r2 = runtime.conversar(sid, ANA, 202508, "contar_pix")                                       # o servidor refaz as contas com contar_pix=True
    est = runtime.estado_bruto(sid)
    assert est["contar_pix"] is True and est["motor"]["falta"] == 63458
    assert r2["acao"] == "mostrar_oferta" and r2["oferta_id"] == "cob_01" and [c["tipo"] for c in r2["cards"]] == ["diagnostico", "comparador"]
    assert r2["finops"]["ferramentas"] == ["listar_ofertas"] and r2["sugestoes"][0]["acao"] == "confirmar"
    assert all(n["origem"] for n in r2["numeros_validados"]) and {634 * 100 + 58, 192} <= {n["valor"] for n in r2["numeros_validados"]}
    r3 = runtime.conversar(sid, ANA, 202508, "confirmar")
    assert r3["acao"] == "abrir_resumo_contrato" and [c["tipo"] for c in r3["cards"]] == ["confirmacao"] and "confirmar_plano" in r3["finops"]["ferramentas"]
    est = runtime.estado_bruto(sid)
    assert est["plano"]["produto"] == "cheque_especial" and est["plano"]["dias"] == 7 and est["historico_contratacoes"][-1]["caminho"] == "cobertura_curta"
    assert [t["ferramenta"] for t in est["trace"] if t.get("llm")] == ["validador"] * 3
    assert "checagens" in [t["ferramenta"] for t in est["trace"]] and est["turnos_llm"] == 3
    # o prompt de sistema do turno tem o contexto, e nunca o texto do cliente
    assert '"primeiro_nome": "Ana"' in est["gi_contexto_json"] and "Sim, o PIX" not in est["gi_contexto_json"]
    assert [h["papel"] for h in est["historico_conversa"]] == ["cliente", "agente"] * 3
    a = runtime.avancar_mes(sid)
    assert a["ciclos_ok"] == 1 and a["cards"][0]["tipo"] == "acompanhamento"
    p = runtime.painel(sid)
    assert p["modo_conversa"] == "gi" and p["validador"]["chamadas"] == 3 and p["validador"]["reprovacoes"] == [] and p["finops"]["chamadas_validador"] == 3


def test_numero_inventado_regenera_uma_vez_e_depois_mensagem_segura(gi):
    ruim = {**OFERTA, "mensagens": ["Faltam R$ 700,00 no dia 30."], "numeros_citados": ["R$ 700,00", "dia 30"]}
    bom = {**PIX}
    f = gi([ruim, bom])
    sid = _sessao(ANA, 202508)
    r = runtime.conversar(sid, ANA, 202508, "ver_opcoes")
    assert len(f.recebidas) == 2 and "não passou nas checagens" in f.recebidas[1] and "R$ 700,00" in f.recebidas[1]
    assert r["validador"]["regeneracoes"] == 1 and r["validador"]["mensagem_segura"] is False and r["acao"] == "nenhuma"
    tr = runtime.estado_bruto(sid)["trace"]
    ch = [t for t in tr if t["ferramenta"] == "checagens"]
    assert ch[0]["falhas"][0]["regra"] == "numeros" and ch[1]["resumo"] == "ok"
    # duas vezes com número fora do contexto: texto fixo, só com números do contexto
    f = gi([ruim, ruim])
    sid = _sessao(ANA, 202508)
    r = runtime.conversar(sid, ANA, 202508, "ver_opcoes")
    assert r["validador"]["mensagem_segura"] is True and r["acao"] == "mostrar_formas_de_pagar" and r["oferta_id"] is None
    assert r["mensagens"][0]["texto"] == "Sua fatura fechou em R$ 2.955,00 e vence dia 30. Veja as formas de pagar."
    assert r["cards"][0]["tipo"] == "opcoes_da_fatura" and "R$ 2.955,00" in r["cards"][0]["dados"]["texto"]
    assert f.validacoes == []                                        # mensagem segura não passa pelo validador
    assert r["guardiao"]["removidos"] == []


def test_validador_reprova_regenera_e_reprova_de_novo(gi):
    f = gi([OFERTA_BRUNO, OFERTA_BRUNO], [_reprovado("R6", "promete que vai dar certo"), _reprovado("R12", "termo técnico")])
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "ver_opcoes")
    assert len(f.recebidas) == 2 and "reprovada pelo validador" in f.recebidas[1] and "R6" in f.recebidas[1] and "corrija" in f.recebidas[1]
    assert r["validador"]["regeneracoes"] == 1 and r["validador"]["mensagem_segura"] is True and r["validador"]["aprovado"] is False
    assert r["validador"]["violacoes"][0]["regra"] == "R12" and r["acao"] == "mostrar_formas_de_pagar"
    est = runtime.estado_bruto(sid)
    assert [x["violacoes"][0]["regra"] for x in est["validador_reprovacoes"]] == ["R6", "R12"]
    assert est["finops"]["chamadas_validador"] == 2 and [t for t in est["trace"] if t["ferramenta"] == "mensagem_segura"]
    p = runtime.painel(sid)
    assert len(p["validador"]["reprovacoes"]) == 2


def test_validador_reprova_e_regeneracao_aprovada(gi):
    f = gi([OFERTA_BRUNO, OFERTA_BRUNO], [_reprovado("R5", "julga"), APROVADO])
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "ver_opcoes")
    assert r["validador"]["aprovado"] is True and r["validador"]["regeneracoes"] == 1 and r["validador"]["mensagem_segura"] is False
    assert r["acao"] == "mostrar_oferta" and len(f.validacoes) == 2


def test_r8_vai_direto_para_uma_pessoa(gi):
    f = gi([OFERTA_BRUNO], [_reprovado("R8", "cliente em sofrimento")])
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "Não tenho dinheiro nem para o aluguel, estou devendo em todo lugar.")
    assert len(f.recebidas) == 1                                            # R8 não regenera
    assert r["acao"] == "transferir_humano" and r["cards"][0]["tipo"] == "encaminhamento" and r["cards"][0]["dados"]["status"] == "encaminhado"
    assert r["sugestoes"] == [] and r["oferta_id"] is None and not checagens.extrair(r["mensagens"][0]["texto"])
    assert runtime.estado_bruto(sid)["encaminhado"]


def test_oferta_inexistente_e_acao_sem_consentimento_viram_mensagem_segura(gi):
    gi([{**OFERTA_BRUNO, "oferta_id": "con_99"}])
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "ver_opcoes")
    assert r["validador"]["mensagem_segura"] and "oferta" in r["validador"]["motivo_mensagem_segura"]
    gi([{"mensagens": ["Sua fatura fechou em R$ 3.619,95 e vence dia 20."], "acao": "mostrar_oferta", "oferta_id": "con_01", "numeros_citados": ["R$ 3.619,95", "dia 20"]}])
    sid = _sessao(BRUNO, 202509, consentir=False)
    r = runtime.conversar(sid, BRUNO, 202509, "consigo_pagar")
    assert r["validador"]["mensagem_segura"] and r["acao"] == "mostrar_formas_de_pagar"
    est = runtime.estado_bruto(sid)
    assert est["motor"] is None and not any(t["ferramenta"] == "capacidade.motor" for t in est["trace"])   # sem consentimento, nada lido


def test_transferir_e_revogar_viram_ferramentas(gi):
    gi([{"mensagens": ["Vou te passar para alguém da equipe."], "acao": "transferir_humano", "oferta_id": None, "numeros_citados": []}])
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "falar_com_pessoa")
    assert r["cards"][0]["tipo"] == "encaminhamento" and r["finops"]["ferramentas"] == ["encaminhar_humano"]
    gi([{"mensagens": ["Certo, vou desligar a análise."], "acao": "revogar_consentimento", "oferta_id": None, "numeros_citados": []}])
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "Quero desligar essa análise dos meus dados.")
    est = runtime.estado_bruto(sid)
    assert est["consentimento"] is False and est["motor"] is None and r["cards"][0]["tipo"] == "aviso" and r["sugestoes"][0]["acao"] == "consentir"


def test_validador_indisponivel_nao_derruba_a_conversa(gi, monkeypatch):
    f = gi([OFERTA_BRUNO])

    async def cai(*a, **k):
        return {"aprovado": None, "violacoes": [], "orientacao": None, "bloqueante": False, "r8": False, "corrigivel": False, "json_valido": False,
                "erro": "ServiceUnavailable: 503", "latencia_ms": 1, "tokens_entrada": 0, "tokens_saida": 0, "bruto": "", "modelo": "falso"}
    monkeypatch.setattr(validador, "validar_async", cai)
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "ver_opcoes")
    assert r["acao"] == "mostrar_oferta" and r["validador"]["aprovado"] is None and "503" in r["validador"]["erro"]
    assert any("indisponível" in t["resumo"] for t in runtime.estado_bruto(sid)["trace"] if t["ferramenta"] == "validador")


def test_validador_desligado(gi, monkeypatch):
    f = gi([OFERTA_BRUNO])
    monkeypatch.setenv("VALIDADOR_ATIVO", "false")
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "ver_opcoes")
    assert f.validacoes == [] and r["validador"]["ativo"] is False and r["finops"]["chamadas_validador"] == 0


def test_modo_tools_valida_depois_do_guardiao(gi, monkeypatch):
    monkeypatch.setenv("MODO_CONVERSA", "tools")
    texto = "Sua fatura fechou em R$ 3.619,95 e vence dia 20. Pelo seu mês, cabe pagar R$ 2.700,87 pela conta.\n\nQuer ver as opções?"
    f = gi([texto, "Tudo bem. Sua fatura fechou em R$ 3.619,95 e vence dia 20.\n\nQuer ver as opções?"], [_reprovado("R12", "jargão"), APROVADO])
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "consigo_pagar")
    assert r["modo_conversa"] == "tools" and r["validador"]["aprovado"] is True and r["validador"]["regeneracoes"] == 1
    assert r["mensagens"][0]["texto"].startswith("Tudo bem.") and len(f.validacoes) == 2
    v = f.validacoes[0]["saida"]
    assert v["acao"] in ("nenhuma", "mostrar_oferta") and "R$ 3.619,95" in v["numeros_citados"] and len(v["mensagens"]) == 2
    assert "reprovada pelo validador" in f.recebidas[1]
    # reprovado duas vezes no modo tools: mensagem segura em texto + formas de pagar
    f = gi([texto, texto], [_reprovado("R6"), _reprovado("R6")])
    sid = _sessao(BRUNO, 202509)
    r = runtime.conversar(sid, BRUNO, 202509, "consigo_pagar")
    assert r["validador"]["mensagem_segura"] and r["mensagens"][0]["texto"].startswith("Sua fatura fechou em R$ 3.619,95 e vence dia 20.")
    assert any(c["tipo"] == "opcoes_da_fatura" for c in r["cards"])


def test_insight_gi_e_limite_de_contato(gi):
    ins = {"texto": "Bruno, a fatura fechou em R$ 3.619,95 e vence dia 20. Um crédito pode pagar a fatura, com parcelas que cabem.",
           "botao_primario": {"rotulo": "Conversar com o ia.i", "acao": "abrir_chat"}, "botao_secundario": {"rotulo": "Agora não", "acao": "nenhuma"},
           "numeros_citados": ["R$ 3.619,95", "dia 20"]}
    f = gi([ins, ins])
    sid = _sessao(BRUNO, 202509)
    r = runtime.insight_gi(sid)
    assert r["insight"]["texto"] == ins["texto"] and r["insight"]["botao"] == {"rotulo": "Conversar com o ia.i", "acao": "ver_opcoes"}
    assert r["validador"]["aprovado"] is True and r["nao_enviar"] is False and r["finops"]["chamadas_validador"] == 1
    r2 = runtime.insight_gi(sid)                                             # segunda proativa no mesmo dia: não envia
    assert r2["nao_enviar"] is True and r2["insight"]["estado"] == "nao_enviar" and len(f.validacoes) == 1


def test_gerar_gi_sobre_contexto_ilustrativo(gi):
    ctx = {"modo": "conversa", "gatilho": "pergunta_cliente", "consentimento": True, "cliente": {"primeiro_nome": "Ana", "publico_vulneravel": False},
           "fatura": {"valor": "R$ 1.700", "vencimento": "dia 20"}, "ofertas_liberadas": [{"id": "cob_01", "tipo": "cobertura_cheque_especial", "prazo": "15 dias", "custo_total": "R$ 38"}]}
    saida = {"mensagens": ["Sua fatura fechou em R$ 1.700 e vence dia 20. Uma opção é o cheque especial por 15 dias, custo total R$ 38. Quer ver?"],
             "acao": "mostrar_oferta", "oferta_id": "cob_01", "numeros_citados": ["R$ 1.700", "dia 20", "15 dias", "R$ 38"]}
    gi([saida, ACEITE])
    rs = runtime.gerar_gi(ctx, ["Ver opções", "Quero pagar tudo."])
    assert len(rs) == 2 and rs[0]["saida"]["acao"] == "mostrar_oferta" and rs[0]["checagens"]["ok"] and rs[0]["validador"]["aprovado"] is True
    assert rs[1]["saida"]["acao"] == "abrir_resumo_contrato" and rs[1]["erro"] is None and rs[1]["finops"]["chamadas_validador"] == 1


def test_interpretar_e_texto_de_correcao():
    r = validador.interpretar('{"aprovado": false, "violacoes": [{"regra": "r1", "trecho": "R$ 9", "motivo": "inventado"}], "orientacao_para_regenerar": "use o contexto"}')
    assert r["aprovado"] is False and r["bloqueante"] and r["violacoes"][0]["gravidade"] == "bloqueante" and not r["r8"]
    assert "R1 (bloqueante): inventado" in validador.texto_correcao(r) and "use o contexto" in validador.texto_correcao(r)
    assert validador.interpretar('{"aprovado": true, "violacoes": []}')["aprovado"] is True
    assert validador.interpretar("xx")["aprovado"] is None and validador.interpretar({"aprovado": True, "violacoes": [{"regra": "R4"}]})["aprovado"] is False
    s = validador.saida_json_de_texto("Sua fatura fechou em R$ 1.700.\n\nSão 10 parcelas de R$ 110,51.\n\nQuer?\n\nMais.", "mostrar_oferta", "con_01")
    assert len(s["mensagens"]) == 3 and s["numeros_citados"] == ["R$ 1.700", "R$ 110,51", "10 parcelas"] and s["oferta_id"] == "con_01"
