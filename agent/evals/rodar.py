#!/usr/bin/env python3
"""Roda o golden set (agent/evals/golden.json) contra um ou mais modelos e imprime uma tabela markdown.

    uv run python evals/rodar.py                                   # modelo de MODELO (.env), todos os casos
    uv run python evals/rodar.py --modelos gemini-3.8-flash,gemini-2.5-flash
    uv run python evals/rodar.py --casos gs01_grupo_b_cabe,gs07_seguro_cashback --limite-chamadas 10
    uv run python evals/rodar.py --so-guardiao                     # só os casos sem modelo (0 chamadas)
    uv run python evals/rodar.py --gerar-evalset                   # escreve golden.evalset.json + test_config.json (adk eval)
    uv run python evals/rodar.py --saida evals/resultado.md        # grava a tabela

Por caso: aprovado/reprovado, fidelidade numérica (números citados pelo modelo que tinham origem), trajetória de
ferramentas, termos proibidos, latência por chamada e tokens. No fim: p50/p95, total de chamadas e tokens por modelo.
Cada caso abre uma sessão nova (cliente real do CSV) com o estado inicial descrito no JSON; o consentimento entra
pelo caminho determinístico (runtime.consentir), como na API.
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


def carregar() -> dict:
    with open(AQUI / "golden.json", encoding="utf-8") as f:
        return json.load(f)


def _texto(msgs: list[dict]) -> str:
    return " ".join(m["texto"] for m in msgs if m.get("papel") == "agente")


def _percentuais(texto: str) -> list[str]:
    from cabe_no_bolso.callbacks import numeros_da_frase
    return [n["texto"] for n in numeros_da_frase(texto) if n["tipo"] == "pct"]


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
        if esp["encaminhamento"] and not (formal or "pessoa" in baixo or "time" in baixo):
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
    return {"id": caso["id"], "titulo": caso["titulo"], "aprovado": ok, "falhas": falhas, "trajetoria": [], "cards": [],
            "latencias_ms": [], "tokens_entrada": 0, "tokens_saida": 0, "chamadas": 0, "texto": novo,
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
        return {"id": caso["id"], "titulo": caso["titulo"], "aprovado": False, "falhas": [f"sessão: {s['erro']}"], "trajetoria": [],
                "cards": [], "latencias_ms": [], "tokens_entrada": 0, "tokens_saida": 0, "chamadas": 0, "texto": "", "fidelidade": 0,
                "numeros_citados": 0, "numeros_barrados": 0, "termos_barrados": [], "barrados_detalhe": [], "duracao_ms": 0}
    sid = s["sessao_id"]
    if est.get("consentimento"):
        runtime.consentir(sid, True, modelo=modelo)
    n_trace0 = len(runtime.trace(sid, modelo=modelo))
    textos, cards, ferramentas, lat, tin, tout, chamadas, erros = [], [], [], [], 0, 0, 0, []
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
        tin += int(fin.get("tokens_entrada") or 0)
        tout += int(fin.get("tokens_saida") or 0)
        chamadas += int(fin.get("chamadas_llm") or 0)
    estado = runtime.estado_bruto(sid, modelo=modelo)
    trace_caso = list(estado.get("trace") or [])[n_trace0:]
    guard = estado.get("guardiao") or guard
    ok, falhas, det = verificar(caso, textos, ferramentas, cards, guard, estado.get("numeros_validados") or [], trace_caso)
    if erros:
        ok, falhas = False, falhas + [f"erro: {e}" for e in erros]
    return {"id": caso["id"], "titulo": caso["titulo"], "aprovado": ok, "falhas": falhas, "trajetoria": ferramentas, "cards": cards,
            "latencias_ms": lat, "tokens_entrada": tin, "tokens_saida": tout, "chamadas": chamadas, "texto": " | ".join(textos),
            "duracao_ms": int((time.perf_counter() - t0) * 1000), **det}


def p50_p95(v: list[int]) -> tuple[int | None, int | None]:
    if not v:
        return None, None
    s = sorted(v)
    return s[int(0.5 * (len(s) - 1))], s[int(round(0.95 * (len(s) - 1)))]


def tabela(modelo: str, resultados: list[dict]) -> str:
    linhas = [f"### Modelo `{modelo}`", "",
              "| caso | resultado | fidelidade numérica | trajetória | termos barrados | latência/chamada (ms) | tokens in/out | chamadas |",
              "|---|---|---|---|---|---|---|---|"]
    for r in resultados:
        res = "aprovado" if r["aprovado"] else "reprovado: " + "; ".join(r["falhas"])[:160]
        fid = f"{r['fidelidade']:.0%} ({r['numeros_citados']} ok, {r['numeros_barrados']} barrados" + (": " + "; ".join(r.get("barrados_detalhe") or []) if r.get("barrados_detalhe") else "") + ")"
        traj = " → ".join(r["trajetoria"]) or "(sem ferramenta)"
        termos = ", ".join(sorted(set(r["termos_barrados"]))) or "—"
        lat = ", ".join(str(x) for x in r["latencias_ms"]) or "—"
        linhas.append(f"| {r['id']} | {res} | {fid} | {traj} | {termos} | {lat} | {r['tokens_entrada']}/{r['tokens_saida']} | {r['chamadas']} |")
    todas = [x for r in resultados for x in r["latencias_ms"]]
    p50, p95 = p50_p95(todas)
    aprov = sum(1 for r in resultados if r["aprovado"])
    linhas += ["", f"**{aprov} de {len(resultados)} aprovados** · chamadas ao modelo: {sum(r['chamadas'] for r in resultados)} · "
                   f"tokens entrada/saída: {sum(r['tokens_entrada'] for r in resultados)}/{sum(r['tokens_saida'] for r in resultados)} · "
                   f"latência p50/p95: {p50}/{p95} ms · fidelidade média: {sum(r['fidelidade'] for r in resultados) / max(1, len(resultados)):.0%}", ""]
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modelos", default=None, help="lista separada por vírgula (padrão: env MODELO ou gemini-3.8-flash)")
    ap.add_argument("--casos", default=None, help="ids separados por vírgula (padrão: todos)")
    ap.add_argument("--limite-chamadas", type=int, default=int(os.environ.get("LIMITE_CHAMADAS", "40")), help="para quando passar deste total de chamadas ao modelo")
    ap.add_argument("--so-guardiao", action="store_true", help="roda só os casos sem modelo")
    ap.add_argument("--gerar-evalset", action="store_true", help="escreve golden.evalset.json e test_config.json para `adk eval`")
    ap.add_argument("--saida", default=None, help="arquivo .md para gravar a tabela")
    ap.add_argument("--mostrar-textos", action="store_true", help="imprime as respostas do agente")
    args = ap.parse_args()

    dados = carregar()
    if args.gerar_evalset:
        p = gerar_evalset(dados, AQUI / "golden.evalset.json")
        print(f"evalset gravado em {p} (+ test_config.json). Rodar: cd agent && uv run adk eval cabe_no_bolso evals/golden.evalset.json --config_file_path evals/test_config.json")
        if not args.modelos and not args.casos and not args.so_guardiao:
            return 0

    ids = set(args.casos.split(",")) if args.casos else None
    casos = [c for c in dados["casos"] if not ids or c["id"] in ids]
    if args.so_guardiao:
        casos = [c for c in casos if c.get("modo") == "guardiao"]
    modelos = [m.strip() for m in (args.modelos or os.environ.get("MODELO") or "gemini-3.8-flash").split(",") if m.strip()]
    saida = [f"# Golden set · {time.strftime('%Y-%m-%d %H:%M')} · {len(casos)} casos", ""]
    total_chamadas = 0
    for modelo in modelos:
        resultados = []
        for c in casos:
            if c.get("modo") == "guardiao":
                r = rodar_caso_guardiao(c)
            elif total_chamadas >= args.limite_chamadas:
                r = {"id": c["id"], "titulo": c["titulo"], "aprovado": False, "falhas": ["não rodou: limite de chamadas atingido"], "trajetoria": [],
                     "cards": [], "latencias_ms": [], "tokens_entrada": 0, "tokens_saida": 0, "chamadas": 0, "texto": "", "fidelidade": 0,
                     "numeros_citados": 0, "numeros_barrados": 0, "termos_barrados": [], "barrados_detalhe": [], "duracao_ms": 0}
            else:
                r = rodar_caso_modelo(c, dados["clientes"], modelo)
                total_chamadas += r["chamadas"]
            resultados.append(r)
            marca = "ok " if r["aprovado"] else "X  "
            print(f"[{marca}] {modelo} · {r['id']} · {r['chamadas']} chamada(s) · {', '.join(r['falhas']) if r['falhas'] else 'aprovado'}", file=sys.stderr)
            if args.mostrar_textos and r.get("texto"):
                print("      " + r["texto"][:600].replace("\n", " "), file=sys.stderr)
        saida.append(tabela(modelo, resultados))
    md = "\n".join(saida)
    print(md)
    if args.saida:
        Path(args.saida).write_text(md, encoding="utf-8")
    print(f"\nchamadas ao modelo nesta execução: {total_chamadas}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
