"""Contexto JSON do modo Gi (cabe_no_bolso.contexto): campos do item 3, números do núcleo já formatados, índice com origem."""
import json

import pytest

from cabe_core import capacidade
from cabe_no_bolso import checagens, contexto, prompt_gi
from tests.conftest import ANA, BRUNO, NO_LIMITE

CAMPOS = {"modo", "gatilho", "consentimento", "cliente", "fatura", "capacidade", "parcelas_em_curso", "faturas_abaixo_do_total_12m",
          "credito_usado_12m", "ofertas_liberadas", "mudar_vencimento", "reincidencia_pos_credito", "acompanhamento", "transacoes_90d",
          "entrada_regular_a_confirmar", "historico_conversa"}


def test_ana_conversa_fechamento_com_consentimento(fonte, taxas):
    r = contexto.montar(ANA, 202508, "conversa", "fechamento", True, fonte=fonte, taxas=taxas)
    c = r["contexto"]
    assert CAMPOS <= set(c)
    assert c["cliente"] == {"primeiro_nome": "Ana", "publico_vulneravel": False}
    assert c["fatura"]["valor"] == "R$ 2.955,00" and c["fatura"]["vencimento"] == "dia 30" and c["fatura"]["pagamento_minimo"] == "R$ 443,25"
    assert c["fatura"]["encargos_se_pagar_minimo"] == "R$ 351,65"            # ofertas.continuar_no_rotativo.se_pagar_minimo.custo_1_mes
    assert [f["forma"] for f in c["fatura"]["formas_de_pagar"]] == ["total", "mínimo"]
    cap = c["capacidade"]
    assert cap["saldo_previsto_no_vencimento"] == "R$ 325,80" == cap["folga_mensal"]        # folga do motor
    assert cap["falta_prevista"] == "R$ 2.629,20" and cap["tipo_de_falta"] == "pontual"
    assert cap["proximo_recebimento"] == {"valor": "R$ 5.467,13", "data": "dia 7"} and cap["dias_ate_proximo_recebimento"] == "7 dias"
    assert c["parcelas_em_curso"] == "R$ 42,83" and c["faturas_abaixo_do_total_12m"] == 1
    assert c["credito_usado_12m"] is False and c["reincidencia_pos_credito"] is False
    assert c["mudar_vencimento"] == {"disponivel": False, "datas": []}
    assert c["acompanhamento"] == {"cobertura_ativa": None, "credito_ativo": None}
    of = c["ofertas_liberadas"]
    assert len(of) == 1 and of[0]["id"] == "cob_01" and of[0]["tipo"] == "cobertura_cheque_especial"
    assert of[0]["prazo"] == "7 dias" and of[0]["custo_total"] == "R$ 7,98" and of[0]["cet"] == "1,3% ao mês"   # taxa confirmada: sem 'ilustrativa'
    assert of[0]["amplia_limite"] is False and of[0]["novo_limite"] is None
    assert len(c["transacoes_90d"]) == 5 and c["transacoes_90d"][0] == {"categoria": "Lazer", "valor": "R$ 2.358,33", "parte_da_fatura": "81%"}
    assert c["entrada_regular_a_confirmar"]["valor_mensal"] == "R$ 1.994,62" and c["entrada_regular_a_confirmar"]["tipo"] == "PIX"
    assert c["historico_conversa"] == []
    # nenhum número solto: só strings formatadas e inteiros de contagem
    assert r["ids"] == {"cob_01": 0}
    assert json.dumps(c, ensure_ascii=False)


def test_indice_tem_origem_do_nucleo(fonte, taxas):
    r = contexto.montar(ANA, 202508, "conversa", "fechamento", True, fonte=fonte, taxas=taxas)
    ix = r["indice"]
    assert ix["R$ 2.955,00"] == {"valor": 295500, "origem": "capacidade.motor:fatura.valor"}
    assert ix["dia 30"] == {"valor": 30, "origem": "capacidade.motor:fatura.vencimento_dia"}
    assert ix["R$ 7,98"]["origem"] == "ofertas.montar:opcoes[0].custo_total"
    assert ix["7 dias"]["origem"] == "ofertas.montar:opcoes[0].dias" or ix["7 dias"]["origem"] == "capacidade.motor:dias_ate_recebimento"
    assert ix["R$ 1.994,62"]["origem"] == "capacidade.motor:flags.pix_mensal_mediana"
    assert ix["90 dias"]["origem"] == "config:taxas.yaml:janela_dias"
    assert "1" not in ix or ix["1"]["valor"] == 1
    assert 1 not in {n["valor"] for n in r["numeros"] if n["origem"] == "capacidade.motor:grupo.roladas_com_atual"}   # nunca citado
    assert all("origem" in n and isinstance(n["valor"], (int, float)) for n in r["numeros"])
    # todo texto do índice é reconhecido pelo extrator das checagens
    for texto in ix:
        assert checagens.chaves(texto), texto


def test_bruno_insight_recorrente_com_tres_ofertas(fonte, taxas):
    r = contexto.montar(BRUNO, 202509, "insight", "fechamento", True, fonte=fonte, taxas=taxas)
    c = r["contexto"]
    assert c["modo"] == "insight" and c["cliente"]["primeiro_nome"] == "Bruno"
    assert c["fatura"]["valor"] == "R$ 3.619,95" and c["fatura"]["vencimento"] == "dia 20" and c["fatura"]["encargos_se_pagar_minimo"] == "R$ 430,77"
    assert c["capacidade"]["tipo_de_falta"] == "recorrente" and c["capacidade"]["falta_prevista"] == "R$ 919,08"
    assert c["faturas_abaixo_do_total_12m"] == 5 and c["entrada_regular_a_confirmar"] is None
    ids = [o["id"] for o in c["ofertas_liberadas"]]
    assert ids == ["con_01", "cp_01", "par_01"]                                   # da mais barata para a mais cara, como o núcleo ordenou
    con = c["ofertas_liberadas"][0]
    assert con["tipo"] == "credito_consignado" and con["parcela"] == "R$ 110,51" and con["parcelas"] == 10 and con["prazo"] == "10 meses"
    assert con["custo_total"] == "R$ 186,02" and con["cet"] == "3,5% ao mês, taxa ilustrativa"       # status conferir => ilustrativa
    assert c["ofertas_liberadas"][2]["cet"] == "9,4% ao mês"                                          # confirmada
    assert r["ids"] == {"con_01": 0, "cp_01": 1, "par_01": 2}


def test_sem_consentimento_so_a_fatura(fonte, taxas):
    r = contexto.montar(BRUNO, 202509, "conversa", "pergunta_cliente", False, fonte=fonte, taxas=taxas)
    c = r["contexto"]
    assert c["consentimento"] is False and c["fatura"]["valor"] == "R$ 3.619,95" and c["fatura"]["vencimento"] == "dia 20"
    assert c["fatura"]["encargos_se_pagar_minimo"] == "R$ 430,77"                # travas.custo_rotativo sobre a fatura do mês: sem histórico
    assert c["capacidade"] is None and c["ofertas_liberadas"] == [] and c["transacoes_90d"] is None
    assert c["entrada_regular_a_confirmar"] is None and c["faturas_abaixo_do_total_12m"] is None
    origens = {n["origem"] for n in r["numeros"]}
    assert not any(o.startswith("capacidade.motor") or o.startswith("ofertas.montar") for o in origens)


def test_no_limite_sem_ofertas(fonte, taxas):
    r = contexto.montar(NO_LIMITE, 202512, "conversa", "fechamento", True, fonte=fonte, taxas=taxas)
    c = r["contexto"]
    assert c["faturas_abaixo_do_total_12m"] >= 6 and c["ofertas_liberadas"] == [] and c["capacidade"]["tipo_de_falta"] == "recorrente"


def test_pix_confirmado_some_da_pergunta_e_muda_a_falta(fonte, taxas):
    sem = contexto.montar(ANA, 202508, "conversa", "pergunta_cliente", True, contar_pix=None, fonte=fonte, taxas=taxas)["contexto"]
    nao = contexto.montar(ANA, 202508, "conversa", "pergunta_cliente", True, contar_pix=False, fonte=fonte, taxas=taxas)["contexto"]
    sim = contexto.montar(ANA, 202508, "conversa", "pergunta_cliente", True, contar_pix=True, fonte=fonte, taxas=taxas)["contexto"]
    assert sem["entrada_regular_a_confirmar"] and nao["entrada_regular_a_confirmar"] is None and sim["entrada_regular_a_confirmar"] is None
    assert sim["capacidade"]["falta_prevista"] == "R$ 634,58" and sim["ofertas_liberadas"][0]["custo_total"] == "R$ 1,92"


def test_reaproveita_motor_e_ofertas_da_sessao_e_credito_usado(fonte, taxas):
    from cabe_core import ofertas as ofertas_mod
    from cabe_no_bolso import policy
    m = capacidade.motor(fonte, BRUNO, 202509, taxas)
    o = ofertas_mod.montar(m, m["grupo"], taxas, policy.liberacao_para(BRUNO, taxas), [{"caminho": "parcelamento", "anomes": 202503}])
    r = contexto.montar(BRUNO, 202509, "conversa", "fechamento", True, fonte=fonte, taxas=taxas, motor=m, ofertas=o,
                        historico_contratacoes=[{"caminho": "parcelamento", "anomes": 202503}])
    assert r["contexto"]["credito_usado_12m"] is True and r["contexto"]["ofertas_liberadas"] == []
    r2 = contexto.montar(BRUNO, 202509, "conversa", "fechamento", True, fonte=fonte, taxas=taxas, motor=m,
                         historico_contratacoes=[{"caminho": "parcelamento", "anomes": 202508, "rolou_depois": True}])
    assert r2["contexto"]["reincidencia_pos_credito"] is True


def test_acompanhamento_credito_e_cobertura(fonte, taxas):
    from cabe_core import ofertas as ofertas_mod
    from cabe_no_bolso import policy
    m = capacidade.motor(fonte, BRUNO, 202509, taxas)
    o = ofertas_mod.montar(m, m["grupo"], taxas, policy.liberacao_para(BRUNO, taxas))
    plano = ofertas_mod.plano_de(o, 0, m)
    plano["parcelas_pagas"] = 1
    r = contexto.montar(BRUNO, 202509, "conversa", "acompanhamento", True, fonte=fonte, taxas=taxas, motor=m, ofertas=o, plano=plano)
    ca = r["contexto"]["acompanhamento"]["credito_ativo"]
    assert ca["parcelas_pagas"] == 1 and ca["total_parcelas"] == 10 and ca["proxima_parcela"] == {"valor": "R$ 110,51", "data": "dia 20"}
    assert ca["limite_liberado"] == "R$ 919,08" and r["contexto"]["ofertas_liberadas"] == []      # plano confirmado: nenhuma nova oferta
    ma = capacidade.motor(fonte, ANA, 202508, taxas)
    oa = ofertas_mod.montar(ma, ma["grupo"], taxas, policy.liberacao_para(ANA, taxas))
    pa = ofertas_mod.plano_de(oa, 0, ma)
    ra = contexto.montar(ANA, 202508, "conversa", "acompanhamento", True, fonte=fonte, taxas=taxas, motor=ma, ofertas=oa, plano=pa, status_cobertura="atrasada")
    cob = ra["contexto"]["acompanhamento"]["cobertura_ativa"]
    assert cob == {"valor": "R$ 2.629,20", "data_prevista_salario": "dia 7", "status": "atrasada"}


def test_modo_e_gatilho_invalidos(fonte, taxas):
    with pytest.raises(ValueError):
        contexto.montar(ANA, 202508, "chat", "fechamento", True, fonte=fonte, taxas=taxas)
    with pytest.raises(ValueError):
        contexto.montar(ANA, 202508, "conversa", "outro", True, fonte=fonte, taxas=taxas)


def test_indice_de_contexto_ilustrativo():
    ctx = {"modo": "conversa", "consentimento": True, "fatura": {"valor": "R$ 1.700", "vencimento": "dia 20"},
           "capacidade": {"saldo_previsto_no_vencimento": "R$ 1.280", "proximo_recebimento": {"valor": "R$ 4.100", "data": "dia 5"}},
           "faturas_abaixo_do_total_12m": 7,
           "ofertas_liberadas": [{"id": "cob_01", "tipo": "cobertura_cheque_especial", "prazo": "15 dias", "custo_total": "R$ 38", "parcelas": 12}]}
    ix = contexto.indice_de(ctx)
    assert ix["R$ 1.700"]["valor"] == 170000 and ix["dia 20"]["valor"] == 20 and ix["15 dias"]["valor"] == 15 and ix["12"]["valor"] == 12
    assert "7" not in ix and "cob_01" not in ix and "90 dias" in ix
    assert all(n["origem"].startswith(("contexto:", "config:")) for n in contexto.numeros_de(ix))


def test_prompt_da_gi_preenchido_com_o_contexto(fonte, taxas):
    r = contexto.montar(ANA, 202508, "conversa", "fechamento", True, fonte=fonte, taxas=taxas)
    p = prompt_gi.prompt_agente(r["contexto"])
    assert "{{" not in p and '"primeiro_nome": "Ana"' in p and "Exemplo C16." in p and "Entrada regular a confirmar" in p
    assert p.startswith("Você é o Cabe no Bolso") and "<diretrizes_itau>" in p and "<contexto>" in p
    b = prompt_gi.blocos()
    assert prompt_gi.conteudo_preservado() and len(prompt_gi.exemplos_lista()) == 22
    assert sum(1 for e in prompt_gi.exemplos_lista() if e["modo"] == "insight") == 6
    assert b["validador"].startswith("Você é o validador") and "R20." in b["validador"]
    v = prompt_gi.prompt_validador(r["contexto"], None, [{"papel": "cliente", "texto": "Ver opções"}], {"mensagens": ["oi"], "acao": "nenhuma"})
    assert "{{" not in v and '"papel": "cliente"' in v and '"mensagens"' in v
    assert "PIX" in prompt_gi.diretrizes_itau() and "sujeito a" in prompt_gi.diretrizes_itau()
