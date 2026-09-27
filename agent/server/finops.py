"""FinOps na borda: custo por turno e por sessão em USD, log JSON por turno (Cloud Logging) e o bloco `finops` do painel.

Preço, custo e projeção vêm de cabe_core.finops (config/finops.yaml). Aqui só se junta ao estado da sessão e se escreve
o log. O log é uma linha JSON por papel por turno no stdout: o Cloud Run a lê como jsonPayload (severity, evento=turno,
sessao_id, ordem, papel, modelo, chamadas, tokens_entrada, tokens_saida, latencia_ms, custo_usd, validador_aprovado,
regeneracoes, mensagem_segura, modo, gatilho, acao). Sem PII e sem conteúdo do extrato: sessao_id é um token aleatório,
`acao` é o nome do chip (nunca o texto do cliente). As métricas baseadas em log estão em deploy/metricas.sh.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from cabe_core import finops as finops_core

EVENTO_TURNO = "turno"


def modelos() -> tuple[str, str]:
    """(modelo do agente, modelo do validador), a mesma leitura de ambiente de cabe_core.finops e agent.py."""
    return finops_core.modelo_padrao(), finops_core.modelo_validador_padrao()


def logs_ligados() -> bool:
    return (os.environ.get("LOG_TURNOS") or "true").strip().lower() not in ("0", "false", "nao", "não", "off")


# ------------------------------------------------------------------ por turno
def custo_do_turno(turno: dict, modelo: str | None = None, modelo_validador: str | None = None) -> dict:
    m, mv = modelos()
    return finops_core.custo_por_papel(turno, modelo or m, modelo_validador or mv)


def completar_turno(estado: dict, turno: dict) -> dict:
    """Acrescenta ao registro do turno o custo em USD por papel e escreve o log. Chamado por sessoes.registrar_turno."""
    m, mv = modelos()
    if estado.get("modo") != "llm":
        m = mv = None                              # sem LLM: nenhum modelo, custo zero
    custo = finops_core.custo_por_papel(turno, m, mv)
    turno["custo_usd"] = custo["total"]["custo_usd"] if (m or turno.get("llm")) else 0.0
    turno["custo_agente_usd"] = custo["agente"]["custo_usd"] if m else 0.0
    turno["custo_validador_usd"] = custo["validador"]["custo_usd"] if mv else 0.0
    turno["modelo"] = m if turno.get("llm") else None
    turno["modelo_validador"] = mv if (turno.get("llm") and int(turno.get("chamadas_validador") or 0) > 0) else None
    turno["moeda"] = "USD"
    if logs_ligados():
        for reg in registros_de_log(estado, turno, custo):
            _escrever(reg)
    return turno


def registros_de_log(estado: dict, turno: dict, custo: dict | None = None) -> list[dict]:
    """Uma linha por papel que chamou o modelo neste turno (agente, validador); turno só em código vira uma linha 'codigo'."""
    m, mv = modelos()
    custo = custo or finops_core.custo_por_papel(turno, m, mv)
    v = turno.get("validador") if isinstance(turno.get("validador"), dict) else {}
    base = {"severity": "INFO", "evento": EVENTO_TURNO, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "sessao_id": estado.get("sessao_id"), "ordem": turno.get("ordem"), "modo": turno.get("modo"), "gatilho": turno.get("gatilho"),
            "acao": turno.get("acao"), "modo_conversa": turno.get("modo_conversa"), "llm": bool(turno.get("llm")),
            "validador_aprovado": v.get("aprovado"), "regeneracoes": int(turno.get("regeneracoes") or 0),
            "mensagem_segura": bool(turno.get("mensagem_segura")), "latencia_ms": turno.get("latencia_ms")}
    if not turno.get("llm"):
        return [{**base, "papel": "codigo", "modelo": None, "chamadas": 0, "tokens_entrada": 0, "tokens_saida": 0, "custo_usd": 0.0}]
    out = []
    ag, va = custo["agente"], custo["validador"]
    if ag["chamadas"] or ag["tokens_entrada"] or ag["tokens_saida"]:
        out.append({**base, "papel": "agente", "modelo": ag["modelo"], "chamadas": ag["chamadas"], "tokens_entrada": ag["tokens_entrada"],
                    "tokens_saida": ag["tokens_saida"], "custo_usd": ag["custo_usd"]})
    if va["chamadas"] or va["tokens_entrada"] or va["tokens_saida"]:
        lat_v = [x for x in (turno.get("latencias_validador_ms") or []) if isinstance(x, (int, float))]
        out.append({**base, "papel": "validador", "modelo": va["modelo"], "chamadas": va["chamadas"], "tokens_entrada": va["tokens_entrada"],
                    "tokens_saida": va["tokens_saida"], "custo_usd": va["custo_usd"], "latencia_ms": round(sum(lat_v), 1) if lat_v else None})
    return out or [{**base, "papel": "agente", "modelo": m, "chamadas": 0, "tokens_entrada": 0, "tokens_saida": 0, "custo_usd": 0.0}]


def _escrever(reg: dict) -> None:
    print(json.dumps(reg, ensure_ascii=False, default=str), file=sys.stdout, flush=True)


# ------------------------------------------------------------------ por sessão (bloco finops do painel)
def resumo_sessao(estado: dict, modelo: str | None) -> dict:
    """Bloco `finops` de GET /api/painel: chamadas e tokens por papel, latências, custo acumulado em USD com a fonte do
    preço, projeção para o piloto (só multiplicação) e o teto de chamadas por sessão."""
    f = estado.get("finops") or {}
    lat = sorted(float(x) for x in (f.get("latencias_ms") or []))

    def p(q: float):
        return None if not lat else round(lat[min(len(lat) - 1, int(round(q * (len(lat) - 1))))], 1)

    llm = estado.get("modo") == "llm"
    m, mv = modelos()
    modelo = modelo or m
    custo = finops_core.custo_por_papel(f, modelo if llm else None, mv if llm else None)
    total = custo["total"]["custo_usd"] if llm else 0.0
    preco = finops_core.preco_de(modelo) if llm else None
    turnos = estado.get("turnos") or []
    chamadas_val = int(f.get("chamadas_validador") or 0) or sum(int((t.get("validador") or {}).get("chamadas") or 0) for t in turnos)
    return {
        "chamadas_llm": int(f.get("chamadas_llm", 0)),
        "chamadas_validador": chamadas_val,
        "tokens_entrada": int(f.get("tokens_entrada", 0)),
        "tokens_saida": int(f.get("tokens_saida", 0)),
        "tokens_entrada_validador": int(f.get("tokens_entrada_validador", 0) or 0),
        "tokens_saida_validador": int(f.get("tokens_saida_validador", 0) or 0),
        "latencia_p50_ms": p(0.5),
        "latencia_p95_ms": p(0.95),
        "custo_estimado": total,
        "custo_acumulado_sessao_usd": total,
        "custo_agente_usd": custo["agente"]["custo_usd"] if llm else 0.0,
        "custo_validador_usd": custo["validador"]["custo_usd"] if llm else 0.0,
        "moeda": "USD",
        "por_papel": {"agente": {**custo["agente"], "modelo": modelo if llm else None}, "validador": {**custo["validador"], "modelo": mv if llm else None}},
        # espelho fino do state do agente (callbacks.somar_por_papel): agente | validador | regeneracao | insight, cada um com o custo
        "chamadas_por_papel": {papel: {**reg, "custo_usd": finops_core.custo_usd(int(reg.get("tokens_entrada") or 0), int(reg.get("tokens_saida") or 0),
                                                                                reg.get("modelo") or (mv if papel == "validador" else modelo))["custo_usd"] if llm else 0.0}
                               for papel, reg in (f.get("chamadas_por_papel") or {}).items() if isinstance(reg, dict)},
        "entradas_bloqueadas": int(f.get("entradas_bloqueadas") or 0),
        "custo_por_turno": [{"ordem": t.get("ordem"), "acao": t.get("acao"), "modo": t.get("modo"), "llm": bool(t.get("llm")),
                             "chamadas_llm": int(t.get("chamadas_llm") or 0), "chamadas_validador": int(t.get("chamadas_validador") or 0),
                             "tokens_entrada": int(t.get("tokens_entrada") or 0), "tokens_saida": int(t.get("tokens_saida") or 0),
                             "latencia_ms": t.get("latencia_ms"), "custo_usd": t.get("custo_usd"), "custo_agente_usd": t.get("custo_agente_usd"),
                             "custo_validador_usd": t.get("custo_validador_usd"), "regeneracoes": int(t.get("regeneracoes") or 0),
                             "mensagem_segura": bool(t.get("mensagem_segura"))} for t in turnos],
        "preco_fonte": preco["fonte"] if preco else "modo sem LLM: nenhuma chamada ao modelo",
        "vigencia": preco["vigencia"] if preco else None,
        "preco_status": preco["status"] if preco else None,
        "preco_entrada_por_milhao_usd": preco["entrada_por_milhao_usd"] if preco else None,
        "preco_saida_por_milhao_usd": preco["saida_por_milhao_usd"] if preco else None,
        "projecao_piloto": finops_core.projecao(total),
        "teto_chamadas_por_sessao": finops_core.teto_chamadas_por_sessao(),
        "modelo": modelo if llm else None,
        "modelo_validador": mv if llm else None,
        "modo": estado.get("modo"),
        "modo_conversa": estado.get("modo_conversa"),
        "nota": (("custo em USD pelo preço com fonte em config/finops.yaml (agente + validador)" if total is not None
                  else "custo_estimado fica null: preço do modelo a conferir em config/finops.yaml")
                 + ("; modo sem LLM: nenhuma chamada ao modelo nesta sessão" if not llm else "")
                 + "; acompanhamento mensal sempre sem LLM"),
    }


__all__ = ["modelos", "custo_do_turno", "completar_turno", "registros_de_log", "resumo_sessao", "logs_ligados", "EVENTO_TURNO"]
