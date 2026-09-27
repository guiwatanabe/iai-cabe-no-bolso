"""Acompanhamento sem LLM: um ciclo por fatura, até 3 faturas inteiras seguidas.

O mês seguinte vem da base quando existe (fatura real e modo real de pagamento); senão é projetado
(fator x compras) e declarado como projeção. Tudo é simulação declarada na tela.
"""
from __future__ import annotations

from . import travas
from .dinheiro import coletar_numeros, mapa_origem


def ciclo(fonte, cliente_id: str, plano: dict, anomes_seguinte: int, taxas: dict | None = None) -> dict:
    """Avança um mês do plano. Devolve o estado do ciclo (e o plano atualizado em 'plano').

    plano: dict de ofertas.plano_de (com o estado corrente ciclos_ok, parcelas_pagas, juros_evitados_acumulados).
    """
    taxas = taxas or {}
    rot = taxas.get("rotativo", {"taxa_mes": 0.14, "teto_encargos_pct": 1.0})
    meta = int(taxas.get("regras_plano", {}).get("faturas_inteiras_para_encerrar", 3))
    anomes = int(anomes_seguinte)
    p = dict(plano)

    real = next((f for f in fonte.faturas(cliente_id, anomes, n=1) if f["anomes"] == anomes), None)
    k = int(p.get("ciclos_total", 0))
    if real:
        fatura, fonte_mes = int(real["fatura"]), "base"
        paga_inteira = real["modo"] == "integral"
    else:
        proj = p.get("proximas_faturas") or []
        fatura = int(proj[min(k, len(proj) - 1)]) if proj else int(p.get("fatura_original", 0))
        fonte_mes = "projecao"
        paga_inteira = fatura <= int(p.get("teto_cartao_mes", 0))

    n = int(p.get("n_parcelas", 1))
    pagas = min(n, int(p.get("parcelas_pagas", 0)) + 1)
    proxima_parcela = int(p["parcela"]) if pagas < n else 0
    ciclos_total = k + 1
    ciclos_ok = int(p.get("ciclos_ok", 0)) + 1 if paga_inteira else 0
    encerrado = ciclos_ok >= meta

    # juros evitados = a mesma comparação que aprovou a opção (rotativo no mesmo horizonte - custo do plano), acumulada por parcela paga
    custo_plano_ate_aqui = int(round(int(p["custo_total"]) * pagas / n))
    rot_horizonte = int(p.get("custo_rotativo_mesmo_horizonte") or travas.custo_rotativo(
        int(p["valor_financiado"]), float(rot["taxa_mes"]), n, float(rot.get("teto_encargos_pct", 1.0))))
    rot_ate_aqui = int(round(rot_horizonte * pagas / n))
    juros_evitados = max(0, rot_ate_aqui - custo_plano_ate_aqui)
    teto = max(0, int(p.get("folga", 0)) - proxima_parcela)

    p.update(ciclos_total=ciclos_total, ciclos_ok=ciclos_ok, parcelas_pagas=pagas, juros_evitados_acumulados=juros_evitados,
             encerrado=encerrado, teto_cartao_mes=teto)
    out = {
        "anomes": anomes,
        "fatura": fatura,
        "fonte_mes": fonte_mes,
        "paga_inteira": bool(paga_inteira),
        "cabe_no_teto": fatura <= teto if teto else False,
        "ciclos_ok": ciclos_ok,
        "ciclos_total": ciclos_total,
        "meta_ciclos": meta,
        "encerrado": bool(encerrado),
        "limite_liberado": int(p["valor_financiado"]),
        "juros_evitados_acumulados": juros_evitados,
        "custo_plano_ate_aqui": custo_plano_ate_aqui,
        "rotativo_ate_aqui": rot_ate_aqui,
        "proxima_parcela": proxima_parcela,
        "parcelas_pagas": pagas,
        "parcelas_restantes": n - pagas,
        "teto_cartao": teto,
        "real": {"modo": real["modo"], "pago": int(real["pago"]), "juros": int(real["juros"])} if real else None,
        "frase": _frase(anomes, paga_inteira, ciclos_ok, meta, encerrado),
        "plano": p,
    }
    out["origem"] = mapa_origem(out, "acompanhar.ciclo", ignorar=("origem", "numeros", "plano"))
    out["numeros"] = coletar_numeros(out, "acompanhar.ciclo", ignorar=("origem", "numeros", "plano"))
    return out


def _frase(anomes: int, paga_inteira: bool, ciclos_ok: int, meta: int, encerrado: bool) -> str:
    from .calendario import rotulo

    mes = rotulo(anomes)
    if encerrado:
        return f"{mes}: fatura paga inteira. {ciclos_ok} de {meta}. Plano encerrado."
    if paga_inteira:
        return f"{mes}: fatura paga inteira. {ciclos_ok} de {meta}. Seguimos."
    return f"{mes}: a fatura não foi paga inteira. Recomeçamos a contagem: 0 de {meta}."
