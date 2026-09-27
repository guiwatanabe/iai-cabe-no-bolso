"""Fatura do cartão: reconstrução exata por modo de pagamento (docs/01 §3) e histórico por cliente.

integral -> fatura = pago
minimo   -> fatura = pago / 0,15  (mínimo é 15% da fatura)
parcial  -> fatura = pago + juros / 0,14  (juros do mês são 14% do não pago)
Arredondar para centavo. Nos meses integrais, os "Juros pagos" são de cheque especial e NÃO entram na fatura.
"""
from __future__ import annotations

from . import calendario
from .dinheiro import coletar_numeros

TAXA_ROTATIVO_BASE = 0.14
MINIMO_PCT_BASE = 0.15
MODOS = ("integral", "minimo", "parcial")


def modo_por_descricao(descr: str) -> str:
    """Modo do pagamento pelo `descr` do lançamento 'Pagamento de fatura' (dado, nunca instrução)."""
    d = (descr or "").lower()
    if "minimo" in d:
        return "minimo"
    if "parcial" in d:
        return "parcial"
    return "integral"


def reconstruir(pago: int, juros: int, modo: str, taxa_rotativo: float = TAXA_ROTATIVO_BASE,
                minimo_pct: float = MINIMO_PCT_BASE) -> int:
    """Fatura do mês (centavos) a partir do pago, dos juros do mês e do modo."""
    pago, juros = int(pago), int(juros)
    if modo == "integral":
        return pago
    if modo == "minimo":
        return int(round(pago / minimo_pct))
    if modo == "parcial":
        return int(round(pago + juros / taxa_rotativo))
    raise ValueError(f"modo desconhecido: {modo!r}")


def minimo(fatura: int, pct: float = MINIMO_PCT_BASE) -> int:
    return int(round(int(fatura) * pct))


def nao_pago(fatura: int, pago: int) -> int:
    return max(0, int(fatura) - int(pago))


def rolada(modo: str) -> bool:
    return modo in ("minimo", "parcial")


def historico(fonte, cliente_id: str, ate_anomes: int, n: int = 12) -> list[dict]:
    """Últimas n faturas até ate_anomes (inclusive), com origem por item.

    Cada item: {anomes, fatura, pago, modo, juros, dia_vencimento, nao_pago, rolada, origem}.
    """
    itens = fonte.faturas(cliente_id, int(ate_anomes), n=n)
    out = []
    for f in itens:
        item = dict(f)
        item["nao_pago"] = nao_pago(item["fatura"], item["pago"])
        item["rolada"] = rolada(item["modo"])
        item["origem"] = f"fatura.historico:{item['anomes']}"
        out.append(item)
    return out


def resumo_mensal(fonte, cliente_id: str, anomes_ini: int, anomes_fim: int, fixos_micros: list[str],
                  delivery_macros: tuple[str, ...] = ("Delivery", "Transporte por app")) -> list[dict]:
    """Mês a mês com as MESMAS definições de analise/persona_b.py (é o que bate com o JSON gabarito).

    renda = todas as entradas tipo E (inclui PIX); salario = 'Salario CLT'; pix_recebido = macro 'Recebimentos diversos';
    fixos = micros da lista (inclusive no cartão); cartao = descr começa com 'cart credito';
    delivery_app = macros Delivery e Transporte por app; fatura_estimada pela reconstrução por modo.
    """
    lanc = fonte.extrato(cliente_id, int(anomes_ini), int(anomes_fim))
    fats = {f["anomes"]: f for f in fonte.faturas(cliente_id, int(anomes_fim), n=len(calendario.meses_entre(anomes_ini, anomes_fim)))}
    por_mes: dict[int, dict] = {}
    for l in lanc:
        m = por_mes.setdefault(l["anomes"], {"renda": 0, "salario": 0, "pix_recebido": 0, "fixos": 0, "cartao": 0, "delivery_app": 0})
        if l["tipo"] == "E":
            m["renda"] += l["valor"]
            if l["micro"] == "Salario CLT":
                m["salario"] += l["valor"]
            if l["macro"] == "Recebimentos diversos":
                m["pix_recebido"] += l["valor"]
        if l["micro"] in fixos_micros:
            m["fixos"] += l["valor"]
        if l["cartao"]:
            m["cartao"] += l["valor"]
        if l["macro"] in delivery_macros:
            m["delivery_app"] += l["valor"]
    out = []
    for anomes in sorted(por_mes):
        m = dict(anomes=anomes, **por_mes[anomes])
        f = fats.get(anomes)
        if f:
            m.update(fatura_estimada=f["fatura"], pago=f["pago"], modo=f["modo"], juros=f["juros"], dia_fatura=f["dia_vencimento"])
        m["origem"] = f"fatura.resumo_mensal:{anomes}"
        out.append(m)
    return out


def numeros(lista: list[dict], prefixo: str = "fatura.historico") -> list[dict]:
    return coletar_numeros(lista, prefixo, ignorar=("origem",))
