#!/usr/bin/env python3
"""Roda os golden sets contra um ou mais modelos e imprime tabelas markdown.

Dois conjuntos e dois modos de conversa:
  golden.json     16 casos (docs/05 + mentora + spec) sobre clientes reais do CSV, pela API do runtime (conversar);
                  roda no modo tools (agente com ferramentas + guardião) ou no modo gi (prompt da Gi + checagens).
  golden_gi.json  22 exemplos do prompt da Gi com contextos ilustrativos, sempre no modo gi (runtime.gerar_gi):
                  contexto pronto -> agente -> checagens -> validador -> (regenera 1x) -> mensagem segura.

    uv run python evals/rodar.py                                       # ambos os conjuntos, modo de MODO_CONVERSA (padrão gi)
    uv run python evals/rodar.py --conjunto gi --modo gi               # só os 22 da Gi
    uv run python evals/rodar.py --conjunto golden --modo tools --casos gs01_grupo_b_cabe,gs03_grupo_a_cobertura
    uv run python evals/rodar.py --modelos gemini-3.8-flash,gemini-2.5-flash --limite-chamadas 40
    uv run python evals/rodar.py --sem-validador                       # só agente + checagens/guardião
    uv run python evals/rodar.py --so-guardiao                         # só os casos sem modelo (0 chamadas)
    uv run python evals/rodar.py --gerar-evalset                       # golden.evalset.json + test_config.json (adk eval)
    uv run python evals/rodar.py --saida evals/resultado.md            # grava as tabelas

Por caso: aprovado/reprovado (esperado), checagens em código, veredito do validador, latência por chamada (agente e
validador), tokens, ação/oferta_id (modo gi) ou trajetória (modo tools). No fim: p50/p95, chamadas e tokens por modelo.
FinOps: --limite-chamadas vale por agente (agente da conversa e validador contam separados). Cada caso traz o custo em USD
(tokens x preço com fonte de config/finops.yaml, agente e validador separados; cabe_core.finops) e o rodapé soma a rodada.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

FERRAMENTAS_DE_DADOS = ("analisar_fatura", "listar_ofertas", "detalhar_fatura", "capacidade.motor", "ofertas.montar")
FERRAMENTAS_DO_AGENTE = ("registrar_consentimento", "analisar_fatura", "listar_ofertas", "detalhar_fatura",
                         "simular_continuar_no_rotativo", "confirmar_plano", "encaminhar_humano")
VAZIO = {"trajetoria": [], "cards": [], "latencias_ms": [], "latencias_validador_ms": [], "tokens_entrada": 0, "tokens_saida": 0, "chamadas": 0,
         "chamadas_validador": 0, "tokens_entrada_validador": 0, "tokens_saida_validador": 0, "custo_usd": 0.0, "custo_agente_usd": 0.0,
         "custo_validador_usd": 0.0, "texto": "", "fidelidade": 0, "numeros_citados": 0, "numeros_barrados": 0, "termos_barrados": [],
         "barrados_detalhe": [], "duracao_ms": 0, "validador": "—", "checagens": "—", "acao": "—"}


def custo_de(r: dict, modelo: str) -> dict:
    """Custo em USD de um caso (agente e validador separados) pelo preço de config/finops.yaml. Nunca inventa preço."""
    from cabe_core import finops
    from cabe_no_bolso import validador as validador_mod
    c = finops.custo_por_papel({"chamadas_llm": r.get("chamadas", 0), "chamadas_validador": r.get("chamadas_validador", 0),
                                "tokens_entrada": r.get("tokens_entrada", 0), "tokens_saida": r.get("tokens_saida", 0),
                                "tokens_entrada_validador": r.get("tokens_entrada_validador", 0), "tokens_saida_validador": r.get("tokens_saida_validador", 0)},
                               modelo, validador_mod.modelo_configurado())
    return {"custo_usd": c["total"]["custo_usd"], "custo_agente_usd": c["agente"]["custo_usd"], "custo_validador_usd": c["validador"]["custo_usd"]}


def usd(v) -> str:
    return "—" if v is None else f"{v:.4f}"


def carregar(nome: str = "golden.json") -> dict:
    with open(AQUI / nome, encoding="utf-8") as f:
        return json.load(f)


def _texto(msgs: list[dict]) -> str:
    return " ".join(m["texto"] for m in msgs if m.get("papel") == "agente")


def _percentuais(texto: str) -> list[str]:
    from cabe_no_bolso.callbacks import numeros_da_frase
    return [n["texto"] for n in numeros_da_frase(texto) if n["tipo"] == "pct"]


def _resumo_validador(v: dict | None) -> str:
    if not v or not v.get("ativo", True) and v.get("aprovado") is None:
        return "desligado"
    if v.get("aprovado") is True:
        return "aprovado" + (f" (após {v['regeneracoes']} regeneração)" if v.get("regeneracoes") else "")
    if v.get("aprovado") is False:
        regras = ", ".join(x.get("regra", "?") for x in v.get("violacoes") or [])
        return f"reprovado [{regras}]" + (" → mensagem segura" if v.get("mensagem_segura") else "")
    return f"indisponível ({v.get('erro')})" if v.get("erro") else "—"


# ------------------------------------------------------------------ golden.json (clientes reais; conversar)
def verificar(caso: dict, textos: list[str], ferramentas: list[str], cards: list[str], guardiao: dict, numeros: list[dict],
              trace_caso: list[dict]) -> tuple[bool, list[str], dict]:
    """Aplica as verificações de caso['esperado']. Devolve (aprovado, falhas, detalhes)."""
    from cabe_no_bolso.callbacks import guardiao_texto

    esp = caso.get("esperado") or {}
    falhas: list[str] = []
    texto = " ".join(textos)
    baixo = texto.lower()
    usadas = set(ferramentas)

    f = esp.get("ferramentas") or {}
    for t in f.get("todas_de", []):
        if t not in usadas:
            falhas.append(f"ferramenta esperada não chamada: {t}")
    if f.get("algum_de") and not (set(f["algum_de"]) & usadas):
        falhas.append(f"nenhuma das ferramentas {f['algum_de']} foi chamada")
    for t in f.get("nenhum_de", []):
        if t in usadas:
            falhas.append(f"ferramenta proibida chamada: {t}")
    if esp.get("sem_leitura_de_dados"):
        lidas = [t["ferramenta"] for t in trace_caso if t["ferramenta"] in FERRAMENTAS_DE_DADOS and not t.get("bloqueada")]
        if lidas:
            falhas.append(f"dados lidos sem consentimento: {lidas}")
    for termo in esp.get("termos_ausentes", []):
        if termo.lower() in baixo:
            falhas.append(f"termo proibido presente: '{termo}'")
    if esp.get("termos_presentes_algum") and not any(t.lower() in baixo for t in esp["termos_presentes_algum"]):
        falhas.append(f"nenhum dos termos esperados apareceu: {esp['termos_presentes_algum']}")
    for c in esp.get("cards_incluem", []):
        if c not in cards:
            falhas.append(f"card esperado ausente: {c}")
    if "encaminhamento" in esp:
        # oferecer uma pessoa é regra em toda conversa; encaminhar é chamar a ferramenta ou mostrar o card
        formal = ("encaminhar_humano" in usadas) or ("encaminhamento" in cards)
        if esp["encaminhamento"] and not (formal or "pessoa" in baixo or "time" in baixo or "equipe" in baixo):
            falhas.append("encaminhamento esperado e não houve")
        if not esp["encaminhamento"] and formal:
            falhas.append("encaminhamento formal não esperado (ferramenta ou card)")
    # fidelidade numérica: o que o cliente viu passa no guardião com 0 remoções; o que o modelo tentou citar sem origem conta contra ele
    _, rel = guardiao_texto(texto, numeros)
    sem_origem_na_tela = [r for r in rel["removidos"] if r["motivo"] == "número sem origem"]
    if esp.get("numeros_com_origem") and sem_origem_na_tela:
        falhas.append(f"número sem origem chegou à tela: {[r['numeros'] for r in sem_origem_na_tela]}")
    tentados = [r for r in (guardiao.get("removidos") or []) if isinstance(r, dict) and r.get("motivo") == "número sem origem"]
    citados = len([n for n in _todos_numeros(texto)])
    total = citados + sum(len(r.get("numeros") or []) for r in tentados)
    fidelidade = 1.0 if total == 0 else round(citados / total, 3)
    if esp.get("sem_percentual_novo"):
        pcts = _percentuais(texto)
        if pcts:
            falhas.append(f"citou percentual: {pcts}")
    if esp.get("substituicoes_min") and int(guardiao.get("substituicoes") or 0) < int(esp["substituicoes_min"]):
        falhas.append(f"substituições de 'sujeito a' abaixo de {esp['substituicoes_min']}")
    if esp.get("numeros_citados_min") and citados < int(esp["numeros_citados_min"]):
        falhas.append(f"citou {citados} número(s); esperado ao menos {esp['numeros_citados_min']}")
    detalhes = {"fidelidade": fidelidade, "numeros_citados": citados, "numeros_barrados": total - citados,
                "termos_barrados": list(guardiao.get("termos_bloqueados") or []),
                "barrados_detalhe": [f"{'/'.join(r.get('numeros') or [])} em «{(r.get('frase') or '')[:80]}»" for r in tentados]}
    return not falhas, falhas, detalhes


def _todos_numeros(texto: str) -> list[dict]:
    from cabe_no_bolso.callbacks import numeros_da_frase
    return numeros_da_frase(texto)


def rodar_caso_guardiao(caso: dict) -> dict:
    from cabe_no_bolso.callbacks import guardiao_texto
    t0 = time.perf_counter()
    novo, rel = guardiao_texto(caso["texto"], caso.get("numeros_validados") or [])
    ok, falhas, det = verificar(caso, [novo], [], [], {"removidos": rel["removidos"], "termos_bloqueados": rel["termos_bloqueados"],
                                                       "substituicoes": rel["substituicoes"]}, caso.get("numeros_validados") or [], [])
    return {**VAZIO, "id": caso["id"], "titulo": caso["titulo"], "aprovado": ok, "falhas": falhas, "texto": novo,
            "duracao_ms": int((time.perf_counter() - t0) * 1000), **det}


def rodar_caso_modelo(caso: dict, clientes: dict, modelo: str) -> dict:
    from cabe_no_bolso import runtime

    cid = clientes[caso["cliente"]]
    anomes = int(caso["anomes"])
    est = caso.get("estado") or {}
    sim = dict(est.get("simulacao") or {})
    t0 = time.perf_counter()
    s = runtime.criar_sessao(cid, anomes, simulacao=sim or None, modelo=modelo)
    if s.get("erro"):
        return {**VAZIO, "id": caso["id"], "titulo": caso["titulo"], "aprovado": False, "falhas": [f"sessão: {s['erro']}"]}
    sid = s["sessao_id"]
    if est.get("consentimento"):
        runtime.consentir(sid, True, modelo=modelo)
    n_trace0 = len(runtime.trace(sid, modelo=modelo))
    textos, cards, ferramentas, lat, latv, tin, tout, tinv, toutv, chamadas, chv, erros, vals, acoes = [], [], [], [], [], 0, 0, 0, 0, 0, 0, [], [], []
    guard = {"removidos": [], "termos_bloqueados": [], "substituicoes": 0}
    for turno in caso["turnos"]:
        r = runtime.conversar(sid, cid, anomes, turno["entrada"], valor=turno.get("valor"), modelo=modelo)
        if r.get("erro"):
            erros.append(r["erro"])
            continue
        textos.append(_texto(r.get("mensagens") or []))
        cards += [c["tipo"] for c in r.get("cards") or []]
        fin = r.get("finops") or {}
        ferramentas += fin.get("ferramentas") or []
        lat += fin.get("latencias_ms") or []
        latv += [x for x in (fin.get("latencias_validador_ms") or []) if isinstance(x, int)]
        tin += int(fin.get("tokens_entrada") or 0)
        tout += int(fin.get("tokens_saida") or 0)
        tinv += int(fin.get("tokens_entrada_validador") or 0)
        toutv += int(fin.get("tokens_saida_validador") or 0)
        chamadas += int(fin.get("chamadas_llm") or 0)
        chv += int(fin.get("chamadas_validador") or 0)
        if r.get("validador"):
            vals.append(_resumo_validador(r["validador"]))
        if r.get("acao"):
            acoes.append(r["acao"] + (f"/{r['oferta_id']}" if r.get("oferta_id") else ""))
    estado = runtime.estado_bruto(sid, modelo=modelo)
    trace_caso = list(estado.get("trace") or [])[n_trace0:]
    guard = estado.get("guardiao") or guard
    ok, falhas, det = verificar(caso, textos, ferramentas, cards, guard, estado.get("numeros_validados") or [], trace_caso)
    if erros:
        ok, falhas = False, falhas + [f"erro: {e}" for e in erros]
    checks = [t for t in trace_caso if t["ferramenta"] == "checagens"]
    base = {**VAZIO, "id": caso["id"], "titulo": caso["titulo"], "aprovado": ok, "falhas": falhas, "trajetoria": ferramentas, "cards": cards,
            "latencias_ms": lat, "latencias_validador_ms": latv, "tokens_entrada": tin, "tokens_saida": tout, "chamadas": chamadas,
            "chamadas_validador": chv, "tokens_entrada_validador": tinv, "tokens_saida_validador": toutv}
    return {**base, **custo_de(base, modelo), "texto": " | ".join(textos), "validador": "; ".join(vals) or "—",
            "checagens": ("ok" if all(t["resumo"] == "ok" for t in checks) else "; ".join(t["resumo"] for t in checks if t["resumo"] != "ok")) if checks else "—",
            "acao": ", ".join(acoes) or "—", "duracao_ms": int((time.perf_counter() - t0) * 1000), **det}


# ------------------------------------------------------------------ golden_gi.json (contextos ilustrativos; gerar_gi)
def verificar_gi(esp: dict, r: dict, modo: str) -> tuple[bool, list[str]]:
    from cabe_no_bolso import checagens

    falhas: list[str] = []
    s = r.get("saida") or {}
    if r.get("erro"):
        return False, [f"erro: {r['erro']}"]
    if not s:
        return False, ["sem saída"]
    textos = checagens.textos_da_saida(s, modo)
    texto = " ".join(textos)
    baixo = texto.lower()
    if modo == "insight":
        bp = (s.get("botao_primario") or {}).get("acao") if isinstance(s.get("botao_primario"), dict) else None
        if "botao_primario_acao" in esp and bp != esp["botao_primario_acao"]:
            falhas.append(f"botão primário {bp!r}; esperado {esp['botao_primario_acao']!r}")
        if "botao_primario_acao_em" in esp and bp not in esp["botao_primario_acao_em"]:
            falhas.append(f"botão primário {bp!r}; esperado um de {esp['botao_primario_acao_em']}")
        if "texto_max" in esp and len(s.get("texto") or "") > int(esp["texto_max"]):
            falhas.append(f"texto com {len(s.get('texto') or '')} caracteres; máximo {esp['texto_max']}")
    else:
        if "acao" in esp and s.get("acao") != esp["acao"]:
            falhas.append(f"ação {s.get('acao')!r}; esperada {esp['acao']!r}")
        if "acao_em" in esp and s.get("acao") not in esp["acao_em"]:
            falhas.append(f"ação {s.get('acao')!r}; esperada uma de {esp['acao_em']}")
        if "mensagens_max" in esp and len(textos) > int(esp["mensagens_max"]):
            falhas.append(f"{len(textos)} mensagens; máximo {esp['mensagens_max']}")
    if "oferta_id" in esp and s.get("oferta_id") != esp["oferta_id"]:
        falhas.append(f"oferta_id {s.get('oferta_id')!r}; esperado {esp['oferta_id']!r}")
    if "oferta_id_em" in esp and s.get("oferta_id") not in esp["oferta_id_em"]:
        falhas.append(f"oferta_id {s.get('oferta_id')!r}; esperado um de {esp['oferta_id_em']}")
    citadas = set()
    for c in s.get("numeros_citados") or []:
        citadas |= checagens.chaves(str(c))
    for n in esp.get("numeros_citados_contem") or []:
        if not checagens.chaves(n) <= (citadas | checagens.chaves(texto)):
            falhas.append(f"número esperado ausente: {n}")
    for termo in esp.get("termos_ausentes") or []:
        if termo.lower() in baixo:
            falhas.append(f"termo proibido presente: '{termo}'")
    if r.get("mensagem_segura"):
        falhas.append(f"caiu na mensagem segura ({r.get('motivo_segura')})")
    ch = r.get("checagens") or {}
    if ch and not ch.get("ok"):
        falhas.append("checagens finais reprovaram: " + "; ".join(f"{f['regra']}: {f['motivo']}" for f in ch.get("falhas") or [][:3]))
    return not falhas, falhas


def rodar_caso_gi(caso: dict, modelo: str, validar: bool) -> dict:
    from cabe_no_bolso import checagens, runtime

    modo = caso["modo"]
    t0 = time.perf_counter()
    entradas = [t["entrada_cliente"] for t in caso["turnos"]]
    try:
        rs = runtime.gerar_gi(caso["contexto"], entradas, modelo=modelo, validar=validar)
    except Exception as e:
        return {**VAZIO, "id": caso["id"], "titulo": caso["titulo"], "aprovado": False, "falhas": [f"erro: {type(e).__name__}: {str(e)[:120]}"]}
    falhas, textos, lat, latv, tin, tout, tinv, toutv, ch, chv, vals, checks, acoes, regen = [], [], [], [], 0, 0, 0, 0, 0, 0, [], [], [], 0
    for i, turno in enumerate(caso["turnos"]):
        if i >= len(rs):
            falhas.append(f"turno {i + 1} não rodou")
            continue
        r = rs[i]
        ok, f = verificar_gi(turno.get("esperado") or {}, r, modo)
        falhas += [f"t{i + 1}: {x}" for x in f]
        s = r.get("saida") or {}
        textos.append(" ".join(checagens.textos_da_saida(s, modo)) if s else "")
        fin = r.get("finops") or {}
        lat += fin.get("latencias_ms") or []
        latv += [x for x in (fin.get("latencias_validador_ms") or []) if isinstance(x, int)]
        tin += int(fin.get("tokens_entrada") or 0)
        tout += int(fin.get("tokens_saida") or 0)
        tinv += int(fin.get("tokens_entrada_validador") or 0)
        toutv += int(fin.get("tokens_saida_validador") or 0)
        ch += int(fin.get("chamadas_llm") or 0)
        chv += int(fin.get("chamadas_validador") or 0)
        regen += int(r.get("regeneracoes") or 0)
        vs = r.get("validacoes") or []
        if vs:
            partes = []
            for k, v in enumerate(vs):
                if v.get("aprovado") is True:
                    partes.append("aprovado")
                elif v.get("aprovado") is False:
                    partes.append("reprovado [" + ", ".join(x.get("regra", "?") for x in v.get("violacoes") or []) + "]")
                else:
                    partes.append(f"indisponível ({v.get('erro')})")
            resumo_v = " → regenerou → ".join(partes) if len(partes) > 1 else partes[0]
            if r.get("mensagem_segura"):
                resumo_v += " → mensagem segura"
            vals.append(resumo_v)
        else:
            vals.append("desligado" if not validar else "—")
        tent = r.get("tentativas") or []
        checks.append("ok" if tent and tent[-1]["checagens"]["ok"] else ("; ".join(f"{x['regra']}: {x['motivo'][:50]}" for t in tent for x in t["checagens"]["falhas"]) or "—"))
        if modo == "insight":
            bp = s.get("botao_primario") if isinstance(s.get("botao_primario"), dict) else {}
            acoes.append(f"botão {bp.get('acao') if bp else None}")
        else:
            acoes.append(f"{s.get('acao')}" + (f"/{s.get('oferta_id')}" if s.get("oferta_id") else ""))
    base = {**VAZIO, "id": caso["id"], "titulo": caso["titulo"], "aprovado": not falhas, "falhas": falhas, "latencias_ms": lat,
            "latencias_validador_ms": latv, "tokens_entrada": tin, "tokens_saida": tout, "chamadas": ch, "chamadas_validador": chv,
            "tokens_entrada_validador": tinv, "tokens_saida_validador": toutv}
    return {**base, **custo_de(base, modelo),
            "texto": " | ".join(textos), "validador": "; ".join(vals), "checagens": "; ".join(checks), "acao": ", ".join(acoes),
            "regeneracoes": regen, "duracao_ms": int((time.perf_counter() - t0) * 1000), "fidelidade": 1.0 if not any("número" in f for f in falhas) else 0.0}


# ------------------------------------------------------------------ tabelas
def p50_p95(v: list[int]) -> tuple[int | None, int | None]:
    if not v:
        return None, None
    s = sorted(v)
    return s[int(0.5 * (len(s) - 1))], s[int(round(0.95 * (len(s) - 1)))]


def _rodape(resultados: list[dict]) -> str:
    todas = [x for r in resultados for x in r["latencias_ms"]]
    todasv = [x for r in resultados for x in r.get("latencias_validador_ms") or []]
    p50, p95 = p50_p95(todas)
    v50, v95 = p50_p95(todasv)
    aprov = sum(1 for r in resultados if r["aprovado"])
    custos = [r.get("custo_usd") for r in resultados]
    total = None if any(c is None for c in custos) else round(sum(custos), 6)
    ag = [r.get("custo_agente_usd") for r in resultados]
    va = [r.get("custo_validador_usd") for r in resultados]
    return (f"**{aprov} de {len(resultados)} aprovados** · chamadas ao agente: {sum(r['chamadas'] for r in resultados)} · "
            f"chamadas ao validador: {sum(r.get('chamadas_validador', 0) for r in resultados)} · "
            f"tokens entrada/saída (agente + validador): {sum(r['tokens_entrada'] for r in resultados)}/{sum(r['tokens_saida'] for r in resultados)} · "
            f"latência do agente p50/p95: {p50}/{p95} ms · latência do validador p50/p95: {v50}/{v95} ms · "
            f"**custo da rodada: US$ {usd(total)}** (agente US$ {usd(None if any(c is None for c in ag) else round(sum(ag), 6))} · "
            f"validador US$ {usd(None if any(c is None for c in va) else round(sum(va), 6))}; preço de config/finops.yaml)")


def tabela(modelo: str, modo: str, resultados: list[dict]) -> str:
    linhas = [f"### golden.json · modelo `{modelo}` · modo `{modo}`", "",
              "| caso | resultado | fidelidade numérica | trajetória / ação | checagens | validador | termos barrados | latência agente (ms) | latência validador (ms) | tokens in/out | chamadas ag/val | custo US$ (ag + val) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in resultados:
        res = "aprovado" if r["aprovado"] else "reprovado: " + "; ".join(r["falhas"])[:160]
        fid = f"{r['fidelidade']:.0%} ({r['numeros_citados']} ok, {r['numeros_barrados']} barrados" + (": " + "; ".join(r.get("barrados_detalhe") or []) if r.get("barrados_detalhe") else "") + ")"
        traj = " → ".join(r["trajetoria"]) or "(sem ferramenta)"
        if r.get("acao") and r["acao"] != "—":
            traj += f" · {r['acao']}"
        termos = ", ".join(sorted(set(r["termos_barrados"]))) or "—"
        lat = ", ".join(str(x) for x in r["latencias_ms"]) or "—"
        latv = ", ".join(str(x) for x in r.get("latencias_validador_ms") or []) or "—"
        linhas.append(f"| {r['id']} | {res} | {fid} | {traj} | {r.get('checagens', '—')} | {r.get('validador', '—')} | {termos} | {lat} | {latv} | "
                      f"{r['tokens_entrada']}/{r['tokens_saida']} | {r['chamadas']}/{r.get('chamadas_validador', 0)} | "
                      f"{usd(r.get('custo_usd'))} ({usd(r.get('custo_agente_usd'))} + {usd(r.get('custo_validador_usd'))}) |")
    linhas += ["", _rodape(resultados) + f" · fidelidade média: {sum(r['fidelidade'] for r in resultados) / max(1, len(resultados)):.0%}", ""]
    return "\n".join(linhas)


def tabela_gi(modelo: str, resultados: list[dict]) -> str:
    linhas = [f"### golden_gi.json (22 exemplos da Gi) · modelo `{modelo}` · modo `gi`", "",
              "| exemplo | resultado | ação / oferta | checagens | validador | regenerações | latência agente (ms) | latência validador (ms) | tokens in/out | chamadas ag/val | custo US$ (ag + val) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in resultados:
        res = "aprovado" if r["aprovado"] else "reprovado: " + "; ".join(r["falhas"])[:200]
        lat = ", ".join(str(x) for x in r["latencias_ms"]) or "—"
        latv = ", ".join(str(x) for x in r.get("latencias_validador_ms") or []) or "—"
        linhas.append(f"| {r['id']} · {r['titulo'][:40]} | {res} | {r.get('acao', '—')} | {r.get('checagens', '—')} | {r.get('validador', '—')} | "
                      f"{r.get('regeneracoes', 0)} | {lat} | {latv} | {r['tokens_entrada']}/{r['tokens_saida']} | {r['chamadas']}/{r.get('chamadas_validador', 0)} | "
                      f"{usd(r.get('custo_usd'))} ({usd(r.get('custo_agente_usd'))} + {usd(r.get('custo_validador_usd'))}) |")
    linhas += ["", _rodape(resultados), ""]
    return "\n".join(linhas)


# ------------------------------------------------------------------ evalset no formato do adk eval
def gerar_evalset(dados: dict, destino: Path) -> Path:
    """EvalSet (google.adk.evaluation.eval_set) com um EvalCase por caso de modelo: estado inicial em session_input,
    turnos em conversation e trajetória esperada em intermediate_data.tool_uses (a que o runner observou, se houver)."""
    from cabe_no_bolso import runtime  # noqa: F401  (garante o pacote no path; sem chamada ao modelo)
    from cabe_no_bolso.agent import NOME

    casos = []
    for c in dados["casos"]:
        if c.get("modo") == "guardiao":
            continue
        cid = dados["clientes"][c["cliente"]]
        est = c.get("estado") or {}
        sim = est.get("simulacao") or {}
        estado = {"cliente_id": cid, "anomes": int(c["anomes"]), "consentimento": bool(est.get("consentimento")),
                  "historico_contratacoes": list(sim.get("historico_contratacoes") or []),
                  "simulacao": {k: v for k, v in sim.items() if k in ("taxas", "liberacao")} or None,
                  "numeros_validados": [], "trace": [], "ferramentas_usadas": []}
        traj = c.get("esperado", {}).get("ferramentas", {}).get("todas_de") or []
        if est.get("consentimento") and not traj:
            traj = ["analisar_fatura"] if c["turnos"][0]["entrada"] in ("ver_opcoes", "consigo_pagar") else []
            if c["turnos"][0]["entrada"] == "ver_opcoes":
                traj.append("listar_ofertas")
        conv = []
        for i, t in enumerate(c["turnos"]):
            from cabe_no_bolso.runtime import ACOES
            texto = ACOES.get(t["entrada"], t["entrada"])
            conv.append({"invocation_id": f"{c['id']}-{i + 1}",
                         "user_content": {"role": "user", "parts": [{"text": texto}]},
                         "final_response": {"role": "model", "parts": [{"text": ""}]},
                         "intermediate_data": {"tool_uses": [{"name": n, "args": {}} for n in (traj if i == len(c["turnos"]) - 1 else [])],
                                               "tool_responses": [], "intermediate_responses": []}})
        casos.append({"eval_id": c["id"], "conversation": conv,
                      "session_input": {"app_name": NOME, "user_id": cid, "state": estado}, "creation_timestamp": 0.0})
    evalset = {"eval_set_id": "cabe_no_bolso_golden", "name": "Golden set Cabe no Bolso", "description": dados["descricao"],
               "eval_cases": casos, "creation_timestamp": 0.0}
    destino.write_text(json.dumps(evalset, ensure_ascii=False, indent=2), encoding="utf-8")
    (destino.parent / "test_config.json").write_text(json.dumps({"criteria": {"tool_trajectory_avg_score": 1.0}}, indent=2), encoding="utf-8")
    return destino


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conjunto", default="ambos", choices=("golden", "gi", "ambos"), help="golden.json, golden_gi.json ou os dois")
    ap.add_argument("--modo", default=None, choices=("gi", "tools"), help="modo de conversa para golden.json (padrão: MODO_CONVERSA ou gi)")
    ap.add_argument("--modelos", default=None, help="lista separada por vírgula (padrão: env MODELO ou gemini-3.8-flash)")
    ap.add_argument("--casos", default=None, help="ids separados por vírgula (padrão: todos)")
    ap.add_argument("--limite-chamadas", type=int, default=int(os.environ.get("LIMITE_CHAMADAS", "40")), help="teto de chamadas por agente (conversa e validador contam separados)")
    ap.add_argument("--sem-validador", action="store_true", help="não chama o validador")
    ap.add_argument("--so-guardiao", action="store_true", help="roda só os casos sem modelo")
    ap.add_argument("--gerar-evalset", action="store_true", help="escreve golden.evalset.json e test_config.json para `adk eval`")
    ap.add_argument("--saida", default=None, help="arquivo .md para gravar as tabelas")
    ap.add_argument("--mostrar-textos", action="store_true", help="imprime as respostas do agente")
    args = ap.parse_args()

    if args.modo:
        os.environ["MODO_CONVERSA"] = args.modo
    if args.sem_validador:
        os.environ["VALIDADOR_ATIVO"] = "false"
    from cabe_no_bolso import runtime, validador
    modo = runtime.modo_conversa()
    validar = validador.ativo()

    golden = carregar("golden.json")
    if args.gerar_evalset:
        p = gerar_evalset(golden, AQUI / "golden.evalset.json")
        print(f"evalset gravado em {p} (+ test_config.json). Rodar: cd agent && uv run adk eval cabe_no_bolso evals/golden.evalset.json --config_file_path evals/test_config.json")
        if not args.modelos and not args.casos and not args.so_guardiao:
            return 0

    ids = set(args.casos.split(",")) if args.casos else None
    modelos = [m.strip() for m in (args.modelos or os.environ.get("MODELO") or "gemini-3.8-flash").split(",") if m.strip()]
    from cabe_core import finops as finops_core
    saida = [f"# Evals · {time.strftime('%Y-%m-%d %H:%M')} · modo `{modo}` · validador {'ligado' if validar else 'desligado'} "
             f"(modelo do validador `{validador.modelo_configurado()}`) · pensamento `{os.environ.get('PENSAMENTO') or 'budget:0 (padrão)'}`", ""]
    for m in modelos:
        pr = finops_core.preco_de(m)
        saida.append(f"Preço de `{m}`: " + (f"US$ {pr['entrada_por_milhao_usd']} entrada / US$ {pr['saida_por_milhao_usd']} saída por milhão de tokens"
                                            f" ({pr['vigencia']}; fonte: {pr['fonte']})" if pr["confirmado"] else "não confirmado em config/finops.yaml (custo fica —)"))
    saida.append("")
    total_ag = total_val = 0
    for modelo in modelos:
        if args.conjunto in ("gi", "ambos"):
            gi = carregar("golden_gi.json")
            casos = [c for c in gi["casos"] if not ids or c["id"] in ids]
            if args.so_guardiao:
                casos = []
            resultados = []
            for c in casos:
                if total_ag >= args.limite_chamadas or (validar and total_val >= args.limite_chamadas):
                    r = {**VAZIO, "id": c["id"], "titulo": c["titulo"], "aprovado": False, "falhas": ["não rodou: limite de chamadas atingido"]}
                else:
                    r = rodar_caso_gi(c, modelo, validar)
                    total_ag += r["chamadas"]
                    total_val += r.get("chamadas_validador", 0)
                resultados.append(r)
                _imprime(modelo, r, args.mostrar_textos)
            if resultados:
                saida.append(tabela_gi(modelo, resultados))
        if args.conjunto in ("golden", "ambos"):
            casos = [c for c in golden["casos"] if not ids or c["id"] in ids]
            if args.so_guardiao:
                casos = [c for c in casos if c.get("modo") == "guardiao"]
            resultados = []
            for c in casos:
                if c.get("modo") == "guardiao":
                    r = rodar_caso_guardiao(c)
                elif total_ag >= args.limite_chamadas or (validar and total_val >= args.limite_chamadas):
                    r = {**VAZIO, "id": c["id"], "titulo": c["titulo"], "aprovado": False, "falhas": ["não rodou: limite de chamadas atingido"]}
                else:
                    r = rodar_caso_modelo(c, golden["clientes"], modelo)
                    total_ag += r["chamadas"]
                    total_val += r.get("chamadas_validador", 0)
                resultados.append(r)
                _imprime(modelo, r, args.mostrar_textos)
            if resultados:
                saida.append(tabela(modelo, modo, resultados))
    md = "\n".join(saida)
    print(md)
    if args.saida:
        Path(args.saida).write_text(md, encoding="utf-8")
    print(f"\nchamadas nesta execução: agente {total_ag}, validador {total_val}", file=sys.stderr)
    return 0


def _imprime(modelo: str, r: dict, mostrar: bool) -> None:
    marca = "ok " if r["aprovado"] else "X  "
    print(f"[{marca}] {modelo} · {r['id']} · {r['chamadas']} ag / {r.get('chamadas_validador', 0)} val · US$ {usd(r.get('custo_usd'))} · {r.get('validador', '—')} · "
          f"{', '.join(r['falhas']) if r['falhas'] else 'aprovado'}", file=sys.stderr)
    if mostrar and r.get("texto"):
        print("      " + r["texto"][:600].replace("\n", " "), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
