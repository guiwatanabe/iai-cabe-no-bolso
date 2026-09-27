"""GenAI FinOps em código: preço por token, custo em USD, custo por papel, projeção e travas. Funções puras.

Única fonte: config/finops.yaml (preços com fonte e data, piloto, travas). Nenhum número nasce aqui: o custo é
tokens x preço do YAML, e cada retorno traz `origem` e a fonte do preço. Sem preço confirmado (status != confirmada)
o custo daquele modelo fica None e a nota diz "preço a conferir": não se inventa preço.

Usado por: cabe_no_bolso/callbacks.py (custo corrente no state), cabe_no_bolso/runtime.py (finops_de), server/finops.py
(painel, log por turno), evals/rodar.py (custo por caso) e analise/gera_mock_demo.py (mocks).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from . import config

MODELO_PADRAO = "gemini-3.8-flash"        # modelo do evento; o preço dele está em config/finops.yaml (precos)
PADRAO_TETO_SESSAO = 12
PADRAO_TETO_TOOLS = 8
PADRAO_PILOTO = 384
CASAS = 6                      # USD com 6 casas: uma sessão custa milésimos de dólar


def modelo_padrao() -> str:
    """Modelo do agente: env MODELO, senão MODELO_PADRAO (mesma leitura em agent.py, runtime, servidor e evals)."""
    return (os.environ.get("MODELO") or "").strip() or MODELO_PADRAO


def modelo_validador_padrao() -> str:
    """Modelo do validador: env MODELO_VALIDADOR, senão o do agente."""
    return (os.environ.get("MODELO_VALIDADOR") or "").strip() or modelo_padrao()


def caminho() -> Path:
    env = os.environ.get("FINOPS")
    if env:
        return Path(env).expanduser().resolve()
    return config.raiz() / "config" / "finops.yaml"


@lru_cache(maxsize=4)
def _carregar(caminho_str: str) -> dict:
    p = Path(caminho_str)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def carregar(caminho_cfg: str | os.PathLike | None = None) -> dict:
    """Dict do YAML (cacheado por caminho; não mutar). Sem arquivo, {} e os padrões deste módulo."""
    return _carregar(str(caminho_cfg or caminho()))


# ------------------------------------------------------------------ preço
def preco_de(modelo: str | None, cfg: dict | None = None) -> dict:
    """Preço do modelo em USD por milhão de tokens: {modelo, entrada_por_milhao_usd, saida_por_milhao_usd, fonte, vigencia,
    status, confirmado}. Sem entrada no YAML: confirmado=False e fonte 'DESCONHECIDO'."""
    cfg = carregar() if cfg is None else cfg
    bloco = ((cfg.get("precos") or {}).get(modelo or "") or {}) if isinstance(cfg.get("precos"), dict) else {}
    entrada, saida = bloco.get("entrada_por_milhao_usd"), bloco.get("saida_por_milhao_usd")
    confirmado = (bloco.get("status") == "confirmada" and isinstance(entrada, (int, float)) and isinstance(saida, (int, float))
                  and not isinstance(entrada, bool) and not isinstance(saida, bool) and bool(bloco.get("fonte")))
    return {"modelo": modelo, "entrada_por_milhao_usd": float(entrada) if confirmado else None,
            "saida_por_milhao_usd": float(saida) if confirmado else None,
            "fonte": bloco.get("fonte") or "DESCONHECIDO: modelo sem preço em config/finops.yaml",
            "vigencia": bloco.get("vigencia"), "status": bloco.get("status") or "desconhecido", "confirmado": confirmado}


def custo_usd(tokens_entrada: int, tokens_saida: int, modelo: str | None, cfg: dict | None = None) -> dict:
    """Custo em USD de tokens_entrada/tokens_saida no modelo. custo_usd=None sem preço confirmado."""
    p = preco_de(modelo, cfg)
    te, ts = max(0, int(tokens_entrada or 0)), max(0, int(tokens_saida or 0))
    custo = None
    if p["confirmado"]:
        custo = round(te / 1e6 * p["entrada_por_milhao_usd"] + ts / 1e6 * p["saida_por_milhao_usd"], CASAS)
    return {"custo_usd": custo, "tokens_entrada": te, "tokens_saida": ts, "modelo": modelo, "preco_fonte": p["fonte"],
            "vigencia": p["vigencia"], "preco_status": p["status"], "preco_confirmado": p["confirmado"],
            "origem": f"finops.custo_usd:{modelo or 'sem_modelo'}"}


def _soma(valores: list[float | None]) -> float | None:
    """Soma de custos; None se algum for None (preço a conferir) e houver tokens naquele papel."""
    if any(v is None for v in valores):
        return None
    return round(sum(valores), CASAS)


def custo_por_papel(fin: dict, modelo: str | None, modelo_validador: str | None = None, cfg: dict | None = None) -> dict:
    """Custo separado por papel a partir do bloco finops de uma sessão ou de um turno.

    fin traz chamadas_llm (agente), chamadas_validador, tokens_entrada/tokens_saida (totais, agente + validador) e,
    quando o runtime separa, tokens_entrada_validador/tokens_saida_validador. Devolve {agente, validador, total} com
    custo_usd por papel (None só quando o preço daquele modelo não está confirmado e houve tokens nele)."""
    fin = fin or {}
    mv = modelo_validador or modelo
    te, ts = int(fin.get("tokens_entrada", 0) or 0), int(fin.get("tokens_saida", 0) or 0)
    tev, tsv = int(fin.get("tokens_entrada_validador", 0) or 0), int(fin.get("tokens_saida_validador", 0) or 0)
    tea, tsa = max(0, te - tev), max(0, ts - tsv)
    agente = {"papel": "agente", "modelo": modelo, "chamadas": int(fin.get("chamadas_llm", 0) or 0), **custo_usd(tea, tsa, modelo, cfg)}
    validador = {"papel": "validador", "modelo": mv, "chamadas": int(fin.get("chamadas_validador", 0) or 0), **custo_usd(tev, tsv, mv, cfg)}
    partes = [agente["custo_usd"] if (tea or tsa or agente["chamadas"]) else 0.0,
              validador["custo_usd"] if (tev or tsv) else 0.0]
    total = _soma(partes)
    return {"agente": agente, "validador": validador,
            "total": {"custo_usd": total, "tokens_entrada": te, "tokens_saida": ts,
                      "chamadas": agente["chamadas"] + validador["chamadas"], "origem": "finops.custo_por_papel:total"}}


# ------------------------------------------------------------------ projeção e travas
def piloto(cfg: dict | None = None) -> dict:
    cfg = carregar() if cfg is None else cfg
    p = cfg.get("piloto") if isinstance(cfg.get("piloto"), dict) else {}
    try:
        n = int(p.get("clientes") or PADRAO_PILOTO)
    except (TypeError, ValueError):
        n = PADRAO_PILOTO
    return {"clientes": n, "fonte": p.get("fonte") or "padrão do módulo (docs/01 §8: 384 clientes do grupo Rolando)",
            "ciclos_por_cliente": int(p.get("ciclos_por_cliente") or 1)}


def projecao(custo_sessao_usd: float | None, cfg: dict | None = None, clientes: int | None = None) -> dict:
    """Só multiplicação: custo desta sessão x clientes do piloto. Rótulo 'projeção': não é medição."""
    p = piloto(cfg)
    n = int(clientes or p["clientes"])
    custo = None if custo_sessao_usd is None else round(float(custo_sessao_usd) * n * p["ciclos_por_cliente"], CASAS)
    return {"rotulo": "projeção", "clientes": n, "ciclos_por_cliente": p["ciclos_por_cliente"], "custo_usd": custo,
            "base": "custo acumulado desta sessão x clientes do piloto (uma conversa por cliente por ciclo; acompanhamento sem LLM)",
            "fonte_clientes": p["fonte"], "origem": "finops.projecao:custo_usd"}


def travas(cfg: dict | None = None) -> dict:
    cfg = carregar() if cfg is None else cfg
    return dict(cfg.get("travas") or {}) if isinstance(cfg.get("travas"), dict) else {}


def teto_chamadas_por_sessao(cfg: dict | None = None) -> int:
    """Teto de chamadas ao modelo por sessão: env CHAMADAS_LLM_POR_SESSAO_MAX > finops.yaml > 12."""
    t = travas(cfg).get("chamadas_llm_por_sessao_max")
    try:
        return int(os.environ.get("CHAMADAS_LLM_POR_SESSAO_MAX") or t or PADRAO_TETO_SESSAO)
    except (TypeError, ValueError):
        return PADRAO_TETO_SESSAO


def teto_tool_calls_por_turno(cfg: dict | None = None) -> int:
    """Teto de chamadas de ferramenta por turno (invocação): env TOOL_CALLS_POR_TURNO_MAX > finops.yaml > 8."""
    t = travas(cfg).get("tool_calls_por_turno_max")
    try:
        return int(os.environ.get("TOOL_CALLS_POR_TURNO_MAX") or t or PADRAO_TETO_TOOLS)
    except (TypeError, ValueError):
        return PADRAO_TETO_TOOLS


def formatar_usd(valor: float | None, casas: int = 4) -> str:
    """Só para textos de relatório (evals, docs). A demo formata no navegador."""
    if valor is None:
        return "—"
    return f"US$ {valor:.{casas}f}".replace(".", ",")


__all__ = ["carregar", "caminho", "preco_de", "custo_usd", "custo_por_papel", "piloto", "projecao", "travas",
           "teto_chamadas_por_sessao", "teto_tool_calls_por_turno", "formatar_usd", "modelo_padrao", "modelo_validador_padrao",
           "MODELO_PADRAO", "CASAS"]
