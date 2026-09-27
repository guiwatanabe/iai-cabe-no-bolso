"""Núcleo determinístico: reproduz o gabarito da persona 3e7d20b2 e os números das duas personas da demo."""
import pytest

from cabe_core import acompanhar, anomalia, calendario, capacidade, dinheiro, fatura, grupo, ofertas, painel
from cabe_no_bolso import policy
from tests.conftest import ANA, BRUNO


# ------------------------------------------------------------------ reconstrução da fatura (docs/01 §3)
def test_reconstruir_por_modo():
    assert fatura.reconstruir(74611, 0, "integral") == 74611
    assert fatura.reconstruir(44325, 35164, "minimo") == 295500          # Ana ago/2025: 443,25 / 0,15
    assert fatura.reconstruir(154566, 29040, "parcial") == 361995        # Bruno set/2025: 1.545,66 + 290,40 / 0,14
    assert fatura.reconstruir(375015, 5380, "integral") == 375015        # juros de cheque especial não entram
    with pytest.raises(ValueError):
        fatura.reconstruir(1, 1, "outro")


def test_modo_por_descricao_e_minimo():
    assert fatura.modo_por_descricao("pgto fatura minimo") == "minimo"
    assert fatura.modo_por_descricao("pgto fatura parcial") == "parcial"
    assert fatura.modo_por_descricao("pgto fatura integral") == "integral"
    assert fatura.minimo(295500) == 44325


# ------------------------------------------------------------------ (a) gabarito mês a mês
def test_gabarito_3e7d20b2_mes_a_mes(fonte, taxas, gabarito_bruno):
    meses = fatura.resumo_mensal(fonte, BRUNO, 202501, 202512, taxas["fixos"]["micros"])
    assert len(meses) == 12 == len(gabarito_bruno["meses"])
    for calc, esperado in zip(meses, gabarito_bruno["meses"]):
        for campo in ("anomes", "fatura_estimada", "pago", "modo", "juros", "fixos", "cartao", "renda", "salario",
                      "pix_recebido", "delivery_app", "dia_fatura"):
            assert calc[campo] == esperado[campo], (calc["anomes"], campo, calc[campo], esperado[campo])


def test_gabarito_3e7d20b2_resumo(fonte, gabarito_bruno):
    hist = fatura.historico(fonte, BRUNO, 202512, 12)
    r = gabarito_bruno["resumo"]
    assert len(hist) == 12
    assert sum(1 for f in hist if f["rolada"]) == r["meses_rolados"] == 5
    assert sum(f["juros"] for f in hist) == r["juros_ano"] == 237333
    assert grupo.classificar(hist)["maior_sequencia"] == r["maior_sequencia"] == 4
    assert hist[-1]["dia_vencimento"] == r["dia_fatura"] == 20
    assert all(f["origem"].startswith("fatura.historico:") for f in hist)


# ------------------------------------------------------------------ (b) Bruno, set/2025: rolando -> parcelamento
def test_bruno_202509_rolando_parcelamento(fonte, taxas):
    m = capacidade.motor(fonte, BRUNO, 202509, taxas)
    assert m["fatura"] == {"valor": 361995, "vencimento_dia": 20, "minimo": 54299, "modo_na_base": "parcial",
                           "pago_na_base": 154566, "nao_pago_na_base": 207429}
    assert m["renda_recorrente"] == 378742 and m["dia_recebimento"] == 7
    assert m["grupo"]["grupo"] == "rolando" and m["grupo"]["roladas_12m"] == 4
    assert m["falta"] > 0 and not m["cabe"] and m["tipo_falta"] == "estrutural"
    assert m["frase"].startswith("faltam R$ 919,08 todo mês")

    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(BRUNO, taxas))
    assert o["caminho"] == "parcelamento" and o["opcoes"] and o["recomendada"] == 0
    assert o["valor_financiado"] == m["falta"] and o["pagar_agora"] + o["valor_financiado"] == 361995
    custos = [op["custo_total"] for op in o["opcoes"]]
    assert custos == sorted(custos)                                           # da mais barata para a mais cara
    for op in o["opcoes"]:
        assert op["parcela"] <= m["folga"] and op["cabe"] and op["liberado"] and not op["bloqueios"]
        assert op["custo_total"] < op["custo_rotativo_mesmo_horizonte"]      # mais barato que continuar no rotativo
        assert op["condicao"] == "depende de aprovação" and "sujeito" not in op["condicao"]
    assert o["opcoes"][0]["produto"] == "consignado_clt"                     # elegível e liberado: vem primeiro
    assert [op["produto"] for op in o["opcoes"]] == ["consignado_clt", "credito_pessoal", "parcelamento_fatura"]
    assert o["teto_cartao_mes"] == m["folga"] - o["opcoes"][0]["parcela"]
    assert o["continuar_no_rotativo"]["custo_1_mes"] == round(m["falta"] * 0.14)
    assert o["continuar_no_rotativo"]["se_pagar_minimo"]["nao_pago"] == 361995 - 54299


# ------------------------------------------------------------------ (c) Ana, ago/2025: escorregão -> cobertura curta
def test_ana_202508_escorregao_cobertura_curta(fonte, taxas):
    m = capacidade.motor(fonte, ANA, 202508, taxas)
    assert m["fatura"]["valor"] == 295500 and m["fatura"]["minimo"] == 44325 and m["fatura"]["vencimento_dia"] == 30
    assert m["renda_recorrente"] == 546713 and m["dia_recebimento"] == 7
    assert m["grupo"]["grupo"] == "escorregao" and m["grupo"]["roladas_12m"] == 0 and m["grupo"]["roladas_com_atual"] == 1
    assert m["tipo_falta"] == "pontual" and m["dias_ate_recebimento"] == 7
    assert m["frase"] == f"faltam {dinheiro.brl(m['falta'])} no dia 30 e o dinheiro volta em 7 dias"
    assert m["flags"]["pix_regular"] is True and m["flags"]["pix_mensal_mediana"] > 0   # PIX vira flag, não renda

    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(ANA, taxas))
    assert o["caminho"] == "cobertura_curta" and len(o["opcoes"]) == 1 and o["recomendada"] == 0
    op = o["opcoes"][0]
    assert op["produto"] == "cheque_especial" and op["dias"] == 7 and op["cabe"] and op["liberado"]
    assert op["custo_total"] < o["continuar_no_rotativo"]["custo_1_mes"]
    assert op["parcela"] == m["falta"] + op["custo_total"] <= m["renda_recorrente"]
    assert o["continuar_no_rotativo"]["se_pagar_minimo"]["nao_pago"] == 251175


def test_ana_contar_pix_muda_folga(fonte, taxas):
    sem = capacidade.motor(fonte, ANA, 202508, taxas)
    com = capacidade.motor(fonte, ANA, 202508, taxas, contar_pix=True)
    assert com["renda_recorrente"] == sem["renda_recorrente"] + sem["flags"]["pix_mensal_mediana"]
    assert com["folga"] > sem["folga"] and com["falta"] < sem["falta"] and com["flags"]["contar_pix"] is True


# ------------------------------------------------------------------ (i) fator 1,33 só nas projeções
def test_fator_133_so_nas_projecoes(fonte, taxas):
    m = capacidade.motor(fonte, ANA, 202508, taxas)
    compras_julho = sum(l["valor"] for l in fonte.extrato(ANA, 202507, 202507) if l["cartao"])
    assert compras_julho == 290480
    assert m["fatura"]["valor"] == 295500 != round(1.33 * compras_julho)     # fatura do mês: reconstrução exata
    assert m["proximas_faturas"][0] == round(1.33 * m["compras_cartao_mes"])  # projeção: fator x compras
    assert m["proximas_faturas"][1] == m["proximas_faturas"][2] == round(1.33 * m["compras_cartao_mediana"])
    assert m["fator_projecao"] == taxas["fator_projecao_fatura"] == 1.33


# ------------------------------------------------------------------ grupo
def test_grupo_faixas():
    fats = [{"anomes": 202500 + i, "modo": "integral"} for i in range(1, 8)]
    assert grupo.classificar(fats)["grupo"] == "em_dia"
    assert grupo.classificar(fats, modo_atual="minimo")["grupo"] == "escorregao"
    fats3 = fats[:4] + [{"anomes": 202505, "modo": "parcial"}, {"anomes": 202506, "modo": "minimo"}, {"anomes": 202507, "modo": "parcial"}]
    g = grupo.classificar(fats3)
    assert g["grupo"] == "rolando" and g["roladas_12m"] == 3 and g["roladas_seguidas"] == 3
    assert grupo.classificar([{"anomes": 202500 + i, "modo": "minimo"} for i in range(1, 7)])["grupo"] == "no_limite"
    assert grupo.classificar([{"anomes": 202400 + i, "modo": "minimo"} for i in range(1, 13)] + fats)["roladas_12m"] == 5


# ------------------------------------------------------------------ anomalia
def test_anomalia_renda_irregular_sem_recorrente():
    lanc = [{"anomes": 202506 + k, "dia": 5, "tipo": "E", "descr": "pix", "valor": 100000 * (k + 1), "macro": "Recebimentos diversos",
             "micro": "Recebimentos diversos", "cartao": False} for k in range(3)]
    r = anomalia.renda(lanc)
    assert r["renda_irregular"] is True and r["mediana_recorrente"] == 0 and r["metodo"] == "media_movel_lag"


def test_anomalia_gastos_e_composicao(fonte, taxas):
    m = capacidade.motor(fonte, ANA, 202508, taxas)
    comp = m["flags"]["composicao_fatura"]
    assert comp["anomes_compras"] == 202507 and comp["total_compras"] == 290480
    assert comp["categorias"][0]["categoria"] == "Lazer" and comp["categorias"][0]["valor"] == 235833
    assert comp["vezes_acima_do_normal"] > 5
    assert all(g["desvio_pct"] is None or g["desvio_pct"] >= 100 for g in m["flags"]["gastos_atipicos"])


# ------------------------------------------------------------------ acompanhamento sem LLM e painel
@pytest.mark.parametrize("cliente,anomes", [(BRUNO, 202509), (ANA, 202508)])
def test_acompanhar_tres_ciclos_encerra(fonte, taxas, cliente, anomes):
    m = capacidade.motor(fonte, cliente, anomes, taxas)
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(cliente, taxas))
    plano = ofertas.plano_de(o, motor=m)
    ciclos = []
    for k in range(1, 4):
        c = acompanhar.ciclo(fonte, cliente, plano, calendario.anomes_soma(anomes, k), taxas)
        plano = c["plano"]
        ciclos.append(c)
    assert [c["ciclos_ok"] for c in ciclos] == [1, 2, 3]
    assert ciclos[-1]["encerrado"] is True and ciclos[-1]["fonte_mes"] == "base"
    assert ciclos[-1]["juros_evitados_acumulados"] <= plano["juros_evitados_total"]
    assert ciclos[-1]["limite_liberado"] == o["valor_financiado"]
    assert all(c["teto_cartao"] == max(0, plano["folga"] - c["proxima_parcela"]) for c in ciclos)

    j = painel.juri(dict(motor=m, ofertas=o, plano=plano, ciclos=ciclos, historico_faturas=fatura.historico(fonte, cliente, 202512),
                         numeros_validados=m["numeros"] + o["numeros"]))
    assert j["fatura_paga_no_vencimento"] == m["fatura"]["valor"] and j["nao_pago"] == 0 and j["encerrado"]
    assert j["numeros_com_origem"] and all("origem" in n and "valor" in n for n in j["numeros_com_origem"])


def test_painel_comparativo_real_bruno(fonte, taxas, gabarito_bruno):
    hist = fatura.historico(fonte, BRUNO, 202512)
    j = painel.juri(dict(historico_faturas=hist))
    assert j["comparativo_real_2025"]["faturas_roladas"] == 5
    assert j["comparativo_real_2025"]["juros_pagos_total_ano"] == gabarito_bruno["resumo"]["juros_ano"]
    assert j["comparativo_real_2025"]["juros_pagos"] == 90585 + 32000 + 11778 + 26314 + 29040


# ------------------------------------------------------------------ rastreabilidade e utilitários
def test_todo_numero_tem_origem(fonte, taxas):
    m = capacidade.motor(fonte, BRUNO, 202509, taxas)
    assert m["numeros"] and all(n["origem"].startswith("capacidade.motor:") for n in m["numeros"])
    assert m["origem"]["folga"] == "capacidade.motor:folga" and m["origem"]["fatura.valor"] == "capacidade.motor:fatura.valor"
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(BRUNO, taxas))
    assert {n["valor"] for n in o["numeros"]} >= {o["opcoes"][0]["parcela"], o["opcoes"][0]["custo_total"], o["teto_cartao_mes"]}


def test_dados_indisponiveis(fonte, taxas):
    with pytest.raises(capacidade.DadosIndisponiveis):
        capacidade.motor(fonte, "cliente-inexistente", 202509, taxas)
    with pytest.raises(capacidade.DadosIndisponiveis):
        capacidade.motor(fonte, BRUNO, 202601, taxas)


def test_dinheiro():
    assert dinheiro.centavos(1545.66) == 154566 and dinheiro.centavos("0.1") == 10
    assert dinheiro.brl(361995) == "R$ 3.619,95" and dinheiro.brl(-500) == "-R$ 5,00" and dinheiro.brl(7) == "R$ 0,07"
    assert dinheiro.brl_arredondado(262920) == "R$ 2.629" and dinheiro.pct(0.035) == "3,5%"
    assert dinheiro.pmt(100000, 0.0, 4) == 25000 and dinheiro.pmt(91908, 0.035, 10) == 11051
    nums = dinheiro.coletar_numeros({"a": 1, "b": {"c": 2.5, "ok": True}, "l": [3]}, "f")
    assert [(n["valor"], n["origem"]) for n in nums] == [(1, "f:a"), (2.5, "f:b.c"), (3, "f:l[0]")]


def test_calendario():
    assert calendario.anomes_soma(202512, 1) == 202601 and calendario.anomes_soma(202501, -1) == 202412
    assert calendario.janela(202508) == [202505, 202506, 202507]
    assert calendario.dias_ate_recebimento(30, 7) == 7 and calendario.dias_ate_recebimento(20, 7) == 17
    assert calendario.dias_ate_recebimento(5, 7) == 2 and calendario.rotulo(202509) == "set/2025"
