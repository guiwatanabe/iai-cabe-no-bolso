"""Travas de crédito: funções puras que devolvem {permitido, bloqueios: [str]}.

São a policy do produto (docs/05 e Spec da Gi), aplicadas em código, nunca por instrução ao modelo.
cabe_no_bolso/policy.py reexporta estas funções e junta a leitura de config/taxas.yaml.
"""
from __future__ import annotations

from . import calendario


def _res(bloqueios: list[str]) -> dict:
    return {"permitido": not bloqueios, "bloqueios": bloqueios}


def custo_rotativo(valor: int, taxa_mes: float, meses: int, teto_pct: float = 1.0) -> int:
    """Custo de continuar no rotativo por `meses` (juros compostos sobre o não pago), limitado a teto_pct do original."""
    valor = int(valor)
    if valor <= 0 or meses <= 0:
        return 0
    bruto = valor * ((1 + taxa_mes) ** int(meses) - 1)
    return int(round(min(bruto, valor * teto_pct)))


def cabe_no_mes(parcela: int, folga: int) -> dict:
    """Regra que governa as outras: nenhuma parcela passa da folga do mês."""
    b = []
    if int(folga) <= 0:
        b.append("sem folga no mês")
    elif int(parcela) > int(folga):
        b.append("parcela maior que a folga do mês")
    return _res(b)


def cobre_ate_recebimento(valor_total: int, renda_recorrente: int, dias: int | None, teto_dias: int = 25) -> dict:
    """Cobertura curta só se o próximo recebimento cobre o valor (com custo) em até teto_dias."""
    b = []
    if dias is None:
        b.append("sem data de recebimento prevista")
    elif int(dias) > int(teto_dias):
        b.append(f"recebimento em {dias} dias, acima do teto de {teto_dias}")
    if int(valor_total) > int(renda_recorrente):
        b.append("o próximo recebimento não cobre o valor")
    return _res(b)


def mais_barato_que_rotativo(custo_total: int, custo_rotativo_horizonte: int) -> dict:
    b = [] if int(custo_total) < int(custo_rotativo_horizonte) else ["custo total não é menor que continuar no rotativo"]
    return _res(b)


def liberado(produto: str, liberacao: dict) -> dict:
    """Só ofertas que o serviço de crédito liberou (simulado por config)."""
    return _res([] if liberacao.get(produto) else [f"{produto} não liberado pelo serviço de crédito"])


def taxa_disponivel(produto: str, taxas: dict) -> dict:
    """Sem taxa em config/taxas.yaml, o produto não existe para o agente."""
    bloco = taxas.get(produto)
    ok = isinstance(bloco, dict) and (bloco.get("taxa_mes") or bloco.get("taxa_mes_base"))
    return _res([] if ok else [f"{produto} sem taxa configurada"])


def grupo_permite(caminho: str, grupo: str, taxas: dict) -> dict:
    """Grupo como trava: cobertura_curta e parcelamento têm listas de grupos em config."""
    if grupo == "no_limite":
        return _res(["6 ou mais faturas roladas em 12 meses: sem crédito automático"])
    bloco = taxas.get(caminho) or {}
    permitidos = bloco.get("grupos") or []
    return _res([] if grupo in permitidos else [f"{caminho} não se aplica ao grupo {grupo}"])


def parcelamento_disponivel(historico_contratacoes: list[dict] | None, anomes: int, vezes_por_12_meses: int = 1) -> dict:
    """Parcelamento pelo Cabe no Bolso: uma vez a cada 12 meses."""
    hist = historico_contratacoes or []
    inicio = calendario.anomes_soma(int(anomes), -11)
    n = sum(1 for h in hist if h.get("caminho") == "parcelamento" and inicio <= int(h.get("anomes", 0)) <= int(anomes))
    return _res([] if n < vezes_por_12_meses else ["já houve parcelamento pelo Cabe no Bolso nos últimos 12 meses"])


def cobertura_disponivel(conta_voltou_positivo: bool) -> dict:
    """Cobertura pode repetir, desde que a conta tenha voltado ao positivo desde o último uso."""
    return _res([] if conta_voltou_positivo else ["a conta não voltou ao positivo desde o último uso do cheque especial"])


def sem_reincidencia(historico_contratacoes: list[dict] | None, anomes: int, meses: int = 3) -> dict:
    """Pedalada em até `meses` depois de um aceite bloqueia nova oferta e chama humano."""
    hist = historico_contratacoes or []
    for h in hist:
        aceite = int(h.get("anomes", 0))
        if h.get("rolou_depois") and aceite <= int(anomes) <= calendario.anomes_soma(aceite, meses):
            return _res([f"voltou a rolar a fatura em até {meses} meses depois do aceite: sem nova oferta; atendimento humano"])
    return _res([])


def perfil_permite(produto: str, perfil: dict) -> dict:
    tipo = (perfil or {}).get("tipo_renda")
    if produto == "consignado_clt" and tipo != "clt":
        return _res(["consignado CLT exige renda CLT"])
    if produto == "consignado_inss" and tipo != "inss":
        return _res(["consignado INSS exige benefício INSS"])
    return _res([])


def requer_confirmacao_humana(produto: str, perfil: dict | None = None) -> bool:
    """Aposentado é público vulnerável: consignado INSS só com confirmação humana (docs/05, princípio 12; spec, casos de borda)."""
    return produto == "consignado_inss"


def juntar(*resultados: dict) -> dict:
    b: list[str] = []
    for r in resultados:
        b.extend(r.get("bloqueios", []))
    return _res(b)
