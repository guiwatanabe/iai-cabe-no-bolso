"""Policy: as travas de crédito de docs/05 e da Spec, aplicadas em código."""
import copy

from cabe_core import capacidade, fatura, grupo, ofertas, travas
from cabe_no_bolso import policy
from tests.conftest import ANA, BRUNO, NO_LIMITE


def _motor_bruno(fonte, taxas):
    return capacidade.motor(fonte, BRUNO, 202509, taxas)


# ------------------------------------------------------------------ (d) 6+ roladas -> nenhum caminho, com motivo
def test_no_limite_sem_credito_automatico(fonte, taxas):
    hist = fatura.historico(fonte, NO_LIMITE, 202511)
    g = grupo.classificar(hist, modo_atual="minimo")
    assert g["grupo"] == "no_limite" and g["roladas_12m"] >= 6

    m = _motor_bruno(fonte, taxas)
    o = ofertas.montar(m, g, taxas, {"consignado_clt": True, "credito_pessoal": True, "parcelamento_fatura": True})
    assert o["caminho"] == "nenhum" and o["opcoes"] == [] and o["recomendada"] is None
    assert "6 ou mais" in o["motivo"] and o["encaminhar_humano"] and o["requer_confirmacao_humana"]
    assert o["opcoes_da_fatura"][0]["valor"] == m["fatura"]["valor"]       # só as formas de pagar a própria fatura


def test_no_limite_na_base_pelo_motor(fonte, taxas):
    m = capacidade.motor(fonte, NO_LIMITE, 202512, taxas)
    assert m["grupo"]["grupo"] == "no_limite"
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(NO_LIMITE, taxas))
    assert o["opcoes"] == [] and o["caminho"] == "nenhum"


# ------------------------------------------------------------------ (e) parcela que não cabe -> nenhuma opção
def test_parcela_nao_cabe_nenhuma_opcao(fonte, taxas):
    m = copy.deepcopy(_motor_bruno(fonte, taxas))
    m["folga"] = 5000                                    # R$ 50 de folga
    m["falta"] = m["fatura"]["valor"] - m["folga"]
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(BRUNO, taxas))
    assert o["caminho"] == "parcelamento" and o["opcoes"] == [] and o["recomendada"] is None
    assert "nenhuma parcela cabe" in o["motivo"] and o["encaminhar_humano"]
    assert all(any("folga" in b for b in op["bloqueios"]) for op in o["descartadas"] if op["liberado"])
    assert travas.cabe_no_mes(6000, 5000) == {"permitido": False, "bloqueios": ["parcela maior que a folga do mês"]}
    assert travas.cabe_no_mes(100, 0)["bloqueios"] == ["sem folga no mês"]


# ------------------------------------------------------------------ (f) segundo parcelamento em 12 meses bloqueado
def test_segundo_parcelamento_em_12_meses_bloqueado(fonte, taxas):
    m = _motor_bruno(fonte, taxas)
    lib = policy.liberacao_para(BRUNO, taxas)
    com_hist = ofertas.montar(m, m["grupo"], taxas, lib, historico_contratacoes=[{"caminho": "parcelamento", "anomes": 202503}])
    assert com_hist["opcoes"] == [] and "12 meses" in com_hist["motivo"] and com_hist["encaminhar_humano"]
    antigo = ofertas.montar(m, m["grupo"], taxas, lib, historico_contratacoes=[{"caminho": "parcelamento", "anomes": 202408}])
    assert antigo["opcoes"]                              # 13 meses atrás: liberado de novo
    assert travas.parcelamento_disponivel([{"caminho": "parcelamento", "anomes": 202409}], 202509)["permitido"] is True   # 12 meses
    assert travas.parcelamento_disponivel([{"caminho": "parcelamento", "anomes": 202410}], 202509)["permitido"] is False  # 11 meses
    assert travas.parcelamento_disponivel([{"caminho": "cobertura_curta", "anomes": 202508}], 202509)["permitido"] is True  # cobertura não conta


def test_reincidencia_apos_aceite_chama_humano(fonte, taxas):
    m = _motor_bruno(fonte, taxas)
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(BRUNO, taxas),
                       historico_contratacoes=[{"caminho": "parcelamento", "anomes": 202507, "rolou_depois": True}])
    assert o["caminho"] == "nenhum" and o["opcoes"] == [] and o["encaminhar_humano"] and "humano" in o["motivo"]


# ------------------------------------------------------------------ (g) taxa ausente no yaml remove o produto
def test_taxa_ausente_remove_produto(fonte, taxas_mutaveis):
    t = taxas_mutaveis
    del t["consignado_clt"]["taxa_mes"]
    assert "consignado_clt" not in policy.produtos_disponiveis(t)
    assert travas.taxa_disponivel("consignado_clt", t)["bloqueios"] == ["consignado_clt sem taxa configurada"]
    m = capacidade.motor(fonte, BRUNO, 202509, t)
    o = ofertas.montar(m, m["grupo"], t, {"consignado_clt": True, "credito_pessoal": True, "parcelamento_fatura": True})
    assert o["opcoes"] and "consignado_clt" not in [op["produto"] for op in o["opcoes"]]
    assert o["opcoes"][0]["produto"] == "credito_pessoal"
    t2 = copy.deepcopy(t)
    del t2["cheque_especial"]
    m2 = capacidade.motor(fonte, ANA, 202508, t2)
    o2 = ofertas.montar(m2, m2["grupo"], t2, {"cheque_especial": True})
    assert o2["caminho"] == "cobertura_curta" and o2["opcoes"] == [] and o2["encaminhar_humano"]


# ------------------------------------------------------------------ (h) consignado INSS exige confirmação humana
def test_consignado_inss_exige_confirmacao_humana(fonte, taxas):
    m = copy.deepcopy(_motor_bruno(fonte, taxas))
    m["perfil"] = {"tipo_renda": "inss"}
    so_inss = ofertas.montar(m, m["grupo"], taxas, {"consignado_inss": True})
    assert [op["produto"] for op in so_inss["opcoes"]] == ["consignado_inss"]
    assert so_inss["opcoes"][0]["requer_confirmacao_humana"] is True
    assert so_inss["recomendada"] is None and so_inss["requer_confirmacao_humana"] is True
    assert travas.requer_confirmacao_humana("consignado_inss") and not travas.requer_confirmacao_humana("credito_pessoal")

    com_pessoal = ofertas.montar(m, m["grupo"], taxas, {"consignado_inss": True, "credito_pessoal": True})
    assert com_pessoal["opcoes"][com_pessoal["recomendada"]]["produto"] == "credito_pessoal"   # INSS nunca é a automática
    clt = ofertas.montar(_motor_bruno(fonte, taxas), m["grupo"], taxas, {"consignado_inss": True, "consignado_clt": True})
    assert "consignado_inss" not in [op["produto"] for op in clt["opcoes"]]                      # CLT não recebe INSS


# ------------------------------------------------------------------ demais travas do item 5
def test_so_ofertas_liberadas(fonte, taxas):
    m = _motor_bruno(fonte, taxas)
    o = ofertas.montar(m, m["grupo"], taxas, {})
    assert o["opcoes"] == [] and all(any("não liberado" in b for b in op["bloqueios"]) for op in o["descartadas"])
    assert policy.liberacao_para("cliente-qualquer", taxas) == {"cheque_especial": False, "parcelamento_fatura": True,
                                                              "credito_pessoal": False, "consignado_clt": False, "consignado_inss": False}


def test_custo_rotativo_com_teto_de_100_por_cento():
    assert travas.custo_rotativo(100000, 0.14, 1) == 14000
    assert travas.custo_rotativo(100000, 0.14, 12, 1.0) == 100000          # Lei 14.690: encargos <= 100% do original
    assert travas.custo_rotativo(0, 0.14, 3) == 0
    assert travas.mais_barato_que_rotativo(999, 1000)["permitido"] and not travas.mais_barato_que_rotativo(1000, 1000)["permitido"]


def test_cobertura_so_ate_25_dias_e_se_o_recebimento_cobre(fonte, taxas):
    assert travas.cobre_ate_recebimento(2000, 5000, 7)["permitido"]
    assert travas.cobre_ate_recebimento(2000, 5000, 26)["bloqueios"] == ["recebimento em 26 dias, acima do teto de 25"]
    assert travas.cobre_ate_recebimento(6000, 5000, 7)["bloqueios"] == ["o próximo recebimento não cobre o valor"]
    assert not travas.cobre_ate_recebimento(1, 1, None)["permitido"]
    assert not travas.cobertura_disponivel(False)["permitido"]
    m = copy.deepcopy(capacidade.motor(fonte, ANA, 202508, taxas))
    m["dias_ate_recebimento"] = 26
    m["tipo_falta"] = "estrutural"                                          # escorregão com falta estrutural -> parcelamento
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(ANA, taxas))
    assert o["caminho"] == "parcelamento" and [op["produto"] for op in o["opcoes"]] == ["credito_pessoal"]


def test_grupo_como_trava(taxas):
    assert travas.grupo_permite("cobertura_curta", "escorregao", taxas)["permitido"]
    assert not travas.grupo_permite("cobertura_curta", "rolando", taxas)["permitido"]
    assert travas.grupo_permite("parcelamento", "rolando", taxas)["permitido"]
    assert not travas.grupo_permite("parcelamento", "no_limite", taxas)["permitido"]


def test_fatura_cabe_so_aviso(fonte, taxas):
    m = copy.deepcopy(_motor_bruno(fonte, taxas))
    m.update(cabe=True, falta=0, tipo_falta="nenhuma")
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(BRUNO, taxas))
    assert o["caminho"] == "nenhum" and "cabe" in o["motivo"] and o["opcoes"] == [] and not o["encaminhar_humano"]


def test_renda_irregular_trata_como_no_limite(fonte, taxas):
    m = copy.deepcopy(_motor_bruno(fonte, taxas))
    m["flags"]["renda_irregular"] = True
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(BRUNO, taxas))
    assert o["caminho"] == "nenhum" and o["opcoes"] == [] and o["encaminhar_humano"] and "renda irregular" in o["motivo"]


def test_sem_prestamista_e_sem_produto_fora_do_plano(fonte, taxas):
    assert taxas["permitir_prestamista"] is False
    m = _motor_bruno(fonte, taxas)
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(BRUNO, taxas))
    textos = " ".join(op["rotulo_cliente"] + op["condicao"] for op in o["opcoes"] + o["descartadas"]).lower()
    assert "prestamista" not in textos and "seguro" not in textos and "sujeito a" not in textos
    assert policy.termos_bloqueados("Tem seguro ou cashback?") == ["seguro", "cashback"]
    assert policy.termos_bloqueados("Fica assim: 10 parcelas.") == []


def test_registro_taxas_e_personas(taxas):
    r = policy.registro_taxas()
    assert r["rotativo"]["taxa_mes"] == 0.14 and r["rotativo"]["status"] == "confirmada"
    assert set(policy.produtos_disponiveis(r)) == {"cheque_especial", "parcelamento_fatura", "credito_pessoal", "consignado_clt", "consignado_inss"}
    assert policy.persona_demo(ANA)["apelido"] == "Ana" and policy.persona_demo(BRUNO)["anomes"] == 202509
    assert policy.encaminhar_humano("pediu")["produto"] is None
    assert taxas["preco_modelo"] is None
