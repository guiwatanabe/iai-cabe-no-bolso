"""Painel "como cheguei aqui" para a banca: o que foi pago, o que foi evitado, o comparativo com o 2025 real e os números com origem."""
from __future__ import annotations

from .dinheiro import coletar_numeros, mapa_origem

SIMULADO = [
    "data da conversa",
    "taxas dos produtos de saída (config/taxas.yaml, com fonte e status)",
    "liberação de crédito (serviço do banco simulado por config)",
    "meses seguintes com o plano aplicado (fatura real da base; pagamento pelo plano)",
]


def juri(estado_sessao: dict) -> dict:
    """estado_sessao: {motor, ofertas, plano, ciclos: [...], historico_faturas: [...], numeros_validados: [...]}."""
    motor = estado_sessao.get("motor") or {}
    ofertas = estado_sessao.get("ofertas") or {}
    plano = estado_sessao.get("plano")
    ciclos = estado_sessao.get("ciclos") or []
    hist = estado_sessao.get("historico_faturas") or []
    fatura = int((motor.get("fatura") or {}).get("valor", 0))
    falta = int(motor.get("falta", 0))

    ultimo = ciclos[-1] if ciclos else None
    if plano:
        juros_evitados = int(ultimo["juros_evitados_acumulados"]) if ultimo else max(
            0, int((ofertas.get("continuar_no_rotativo") or {}).get("custo_1_mes", 0)) - int(plano.get("custo_total", 0)))
    else:
        juros_evitados = 0

    roladas = [f for f in hist if f.get("modo") in ("minimo", "parcial")]
    comparativo = {
        "faturas_roladas": len(roladas),
        "juros_pagos": sum(int(f.get("juros", 0)) for f in roladas),
        "juros_pagos_total_ano": sum(int(f.get("juros", 0)) for f in hist),
        "meses_na_base": len(hist),
    }

    numeros = list(estado_sessao.get("numeros_validados") or [])
    vistos, dedup = set(), []
    for n in numeros:
        chave = (n.get("valor"), n.get("origem"))
        if chave not in vistos:
            vistos.add(chave)
            dedup.append(n)

    out = {
        "fatura_paga_no_vencimento": fatura if plano else int(ofertas.get("pagar_agora", fatura - falta)),
        "nao_pago": 0 if plano else falta,
        "juros_evitados": juros_evitados,
        "plano": {"caminho": plano["caminho"], "produto": plano["produto"], "parcela": plano["parcela"], "n_parcelas": plano["n_parcelas"],
                  "custo_total": plano["custo_total"], "termina_em": plano["termina_em"]} if plano else None,
        "ciclos_ok": int(ultimo["ciclos_ok"]) if ultimo else 0,
        "encerrado": bool(ultimo["encerrado"]) if ultimo else False,
        "comparativo_real_2025": comparativo,
        "grupo": (motor.get("grupo") or {}).get("grupo"),
        "tipo_falta": motor.get("tipo_falta"),
        "simulado": SIMULADO,
        "numeros_com_origem": dedup,
    }
    out["origem"] = mapa_origem(out, "painel.juri", ignorar=("origem", "numeros_com_origem"))
    out["numeros"] = coletar_numeros(out, "painel.juri", ignorar=("origem", "numeros_com_origem", "numeros"))
    return out
