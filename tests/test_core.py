import copy
import json
from pathlib import Path

import pytest

from mcp_server.core import taxas as taxas_mod
from mcp_server.core.acompanhamento import acompanhamento
from mcp_server.core.fatura import formas_de_pagar
from mcp_server.core.ofertas import mudar_vencimento, ofertas_liberadas
from mcp_server.core.rotativo import custo_rotativo
from mcp_server.core.tipos import Contexto, EstadoSessao, Liberacao

FIXTURES = Path(__file__).parent / "fixtures"
TAXAS = taxas_mod.carregar()
COM_CONSENTIMENTO = EstadoSessao(consentimento=True)


def ctx(nome: str, **update) -> Contexto:
    base = Contexto.model_validate(json.loads((FIXTURES / f"gold_{nome}.json").read_text()))
    return base.model_copy(update=update)


def ids(ofertas):
    return [o["id"] for o in ofertas]


# Expected numbers below were computed by hand (exact fractions, half-up), not by calling the core twice.


def test_escorregao_gets_cobertura_with_pro_rata_cost():
    [cob] = ofertas_liberadas(ctx("escorregao"), COM_CONSENTIMENTO, TAXAS)
    # 42000 x 0.08 x 15 / 30 = 1680
    assert cob["id"] == "cob_01" and cob["tipo"] == "cobertura_cheque_especial"
    assert cob["custo_total_c"] == 1680 and cob["prazo_dias"] == 15 and cob["taxa_mes"] == 0.08
    assert cob["parcela_c"] is None and cob["parcelas"] is None
    assert cob["amplia_limite"] is False and cob["novo_limite_c"] is None
    assert cob["origem"]["taxa_mes"] == "taxas.yaml:cobertura_curta"


def test_rolando_gets_consignado_cheapest_fitting_prazo_and_no_prestamista():
    ofertas = ofertas_liberadas(ctx("rolando"), COM_CONSENTIMENTO, TAXAS)
    assert ids(ofertas) == ["cons_01"]  # prestamista has taxa_mes null -> absent
    [cons] = ofertas
    # PMT(75000, 3.5%, 10) = 9018; 9018 x 10 - 75000 = 15180
    assert (cons["parcelas"], cons["parcela_c"], cons["custo_total_c"]) == (10, 9018, 15180)
    assert cons["parcela_c"] <= 60000 and cons["prazo_dias"] is None
    assert cons["origem"]["taxa_mes"] == "taxas.yaml:credito_consignado"


def test_folga_forces_longer_prazo():
    # 10x = 9018 > 8000; 12x = 7761 fits; 7761 x 12 - 75000 = 18132
    [cons] = ofertas_liberadas(ctx("rolando", folga_mensal_c=8000), COM_CONSENTIMENTO, TAXAS)
    assert (cons["parcelas"], cons["parcela_c"], cons["custo_total_c"]) == (12, 7761, 18132)


def test_parcela_above_folga_drops_product():
    # longest prazo 24x = 4670 > 4000
    assert ofertas_liberadas(ctx("rolando", folga_mensal_c=4000), COM_CONSENTIMENTO, TAXAS) == []


def test_prestamista_appears_once_it_has_a_rate_and_list_is_sorted():
    taxas = copy.deepcopy(TAXAS)
    taxas["credito_prestamista"]["taxa_mes"] = 0.05
    ofertas = ofertas_liberadas(ctx("rolando"), COM_CONSENTIMENTO, taxas)
    assert ids(ofertas) == ["cons_01", "prest_01"]
    assert ofertas[0]["custo_total_c"] <= ofertas[1]["custo_total_c"]


def test_no_limite_gets_nothing():
    assert ofertas_liberadas(ctx("no_limite"), COM_CONSENTIMENTO, TAXAS) == []


@pytest.mark.parametrize(
    "nome,estado",
    [
        ("escorregao", EstadoSessao()),
        ("rolando", EstadoSessao()),
        ("escorregao", EstadoSessao(consentimento=True, liberacao=Liberacao(cobertura=False))),
        ("rolando", EstadoSessao(consentimento=True, liberacao=Liberacao(consignado=False))),
        ("escorregao", EstadoSessao(consentimento=True, reincidencia_pos_credito=True)),
        ("rolando", EstadoSessao(consentimento=True, reincidencia_pos_credito=True)),
        ("rolando", EstadoSessao(consentimento=True, credito_usado_12m=True)),
        ("escorregao", EstadoSessao(consentimento=True, cheque_especial_zerado=False)),
    ],
)
def test_state_filters_empty_the_list(nome, estado):
    assert ofertas_liberadas(ctx(nome), estado, TAXAS) == []


@pytest.mark.parametrize(
    "nome,update",
    [
        ("escorregao", {"dias_ate_recebimento": 26}),
        ("escorregao", {"qtd_pedaladas_12m": 6}),
        ("escorregao", {"confianca_recebimento": "MEDIA"}),
        ("escorregao", {"tipo_falta": "SEM_FALTA", "valor_faltante_fatura_c": 0}),
        ("escorregao", {"valor_recebimento_tipico_c": 41999}),
        ("escorregao", {"elegivel_cobertura_curta": False}),
        ("rolando", {"elegivel_parcelamento": False}),
    ],
)
def test_context_filters_empty_the_list(nome, update):
    assert ofertas_liberadas(ctx(nome, **update), COM_CONSENTIMENTO, TAXAS) == []


def test_cobertura_at_the_25_day_ceiling():
    # 42000 x 0.08 x 25 / 30 = 2800
    [cob] = ofertas_liberadas(ctx("escorregao", dias_ate_recebimento=25), COM_CONSENTIMENTO, TAXAS)
    assert cob["custo_total_c"] == 2800


def test_publico_vulneravel_is_not_a_filter():
    assert ids(ofertas_liberadas(ctx("escorregao", publico_vulneravel=True), COM_CONSENTIMENTO, TAXAS)) == ["cob_01"]
    assert ids(ofertas_liberadas(ctx("rolando", publico_vulneravel=True), COM_CONSENTIMENTO, TAXAS)) == ["cons_01"]


def test_ampliacao_only_with_configured_limit():
    taxas = copy.deepcopy(TAXAS)
    taxas["cobertura_curta"]["limite_cheque_especial_c"] = 40000  # gap 42000 <= 40000 x 1.10 = 44000
    [cob] = ofertas_liberadas(ctx("escorregao"), COM_CONSENTIMENTO, taxas)
    assert cob["amplia_limite"] is True and cob["novo_limite_c"] == 42000
    taxas["cobertura_curta"]["limite_cheque_especial_c"] = 30000  # 42000 > 33000: no cobertura
    assert ofertas_liberadas(ctx("escorregao"), COM_CONSENTIMENTO, taxas) == []


def test_formas_de_pagar_exact_numbers():
    f = formas_de_pagar(ctx("escorregao"), TAXAS)
    # minimo = 170000 x 0.15 = 25500; encargos = (170000 - 25500) x 0.14 = 20230
    assert (f["valor_c"], f["dia_vencimento"], f["minimo_c"], f["encargos_se_pagar_minimo_c"]) == (
        170000,
        20,
        25500,
        20230,
    )
    assert [(p["parcelas"], p["parcela_c"], p["custo_total_c"]) for p in f["parcelamento_fatura"]] == [
        (3, 67639, 32917),
        (6, 38349, 60094),
        (10, 26958, 99580),
        (12, 24221, 120652),
    ]
    assert f["origem"]["valor_c"] == "gold_contexto_agente"


def test_minimo_rounds_half_up():
    # 170030 x 0.15 = 25504.5 -> 25505 (half-even would give 25504); (170030 - 25505) x 0.14 = 20233.5 -> 20234
    f = formas_de_pagar(ctx("escorregao", fatura_estimada_c=170030), TAXAS)
    assert (f["minimo_c"], f["encargos_se_pagar_minimo_c"]) == (25505, 20234)


def test_rotativo_compound_and_cap():
    assert custo_rotativo(100000, 1, TAXAS)["custo_c"] == 14000
    # 100000 x (1.14^5 - 1) = 92541.45824
    assert custo_rotativo(100000, 5, TAXAS) == {
        "custo_c": 92541,
        "limitado_pelo_teto": False,
        "origem": {"custo_c": "core.rotativo", "limitado_pelo_teto": "core.rotativo"},
    }
    # 100000 x (1.14^6 - 1) = 119497.26 > 100% of the debt -> capped at 100000
    r = custo_rotativo(100000, 6, TAXAS)
    assert (r["custo_c"], r["limitado_pelo_teto"]) == (100000, True)


def test_mudar_vencimento():
    assert mudar_vencimento(ctx("escorregao"))["dias"] == [6, 7]
    assert mudar_vencimento(ctx("escorregao", dia_recebimento_estimado=27))["dias"] == [28, 1]
    assert mudar_vencimento(ctx("escorregao", dia_recebimento_estimado=30))["dias"] == [1, 2]
    assert mudar_vencimento(ctx("rolando"))["disponivel"] is False


def test_acompanhamento():
    assert acompanhamento(ctx("escorregao"), COM_CONSENTIMENTO) is None
    cob = acompanhamento(
        ctx("escorregao"), EstadoSessao(oferta_aceita={"id": "cob_01", "tipo": "cobertura_cheque_especial"})
    )
    assert (cob["tipo"], cob["status"], cob["valor_c"], cob["dia_recebimento"]) == ("cobertura", "aguardando", 42000, 5)
    oferta = {"id": "cons_01", "tipo": "credito_consignado", "parcelas_pagas": 2, "parcela_c": 9018, "parcelas": 10}
    cred = acompanhamento(ctx("rolando"), EstadoSessao(oferta_aceita=oferta))
    assert cred["tipo"] == "credito" and cred["limite_liberado_c"] == 185000
    assert (cred["parcelas_pagas"], cred["parcelas"], cred["proxima_parcela_c"], cred["dia_proxima"]) == (
        2,
        10,
        9018,
        5,
    )
