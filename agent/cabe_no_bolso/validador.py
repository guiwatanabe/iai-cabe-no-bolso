"""Validador (Notas da Gi: "um agente, dois modos, um validador"): segundo LlmAgent, separado, que só aprova ou reprova.

Recebe o contexto, as diretrizes, as últimas mensagens e a saída do agente; devolve JSON
{aprovado, violacoes: [{regra, gravidade, trecho, motivo}], orientacao_para_regenerar}. Nunca escreve para o cliente.
Usado nos dois modos de conversa (no modo tools o runtime embrulha o texto no JSON do formato da Gi antes de validar).

Onde o prompt entra: o texto da Gi com os quatro placeholders preenchidos vai inteiro na mensagem de usuário de uma
sessão descartável (uma por validação); a instrução de sistema é uma linha fixa. Assim o texto do cliente (que está no
histórico) nunca entra no prompt de sistema. Um turno = uma chamada; sem ferramentas; JSON forçado.

Política (Notas, "O que acontece depois da validação"): reprovado => o agente gera de novo uma única vez com as
violações e a orientação; reprovado de novo, ou qualquer R8 => mensagem segura. Toda reprovação vai para o trace.
Se o validador falhar (rede, cota), o runtime segue com a saída que já passou pelas checagens e registra
"validador indisponível" no trace: a demo não cai por causa do revisor.
"""
from __future__ import annotations

import json
import os
import time
import uuid

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from cabe_no_bolso import agent as agent_mod, checagens, prompt_gi

NOME = "validador_cabe_no_bolso"
APP = "cabe_no_bolso_validador"
INSTRUCAO_FIXA = ("Você é o validador do Cabe no Bolso. Siga exatamente o prompt do validador que chega na mensagem e responda "
                  "apenas com o JSON pedido, sem texto fora dele.")
BLOQUEANTES = ("R1", "R2", "R3", "R6", "R8", "R10", "R13", "R14", "R15", "R17", "R18", "R19", "R20")
CORRIGIVEIS = ("R4", "R5", "R7", "R9", "R11", "R12", "R16")

_RUNNERS: dict[str, Runner] = {}


def ativo() -> bool:
    return (os.environ.get("VALIDADOR_ATIVO") or "true").strip().lower() in ("1", "true", "sim", "yes", "on")


def modelo_configurado() -> str:
    return os.environ.get("MODELO_VALIDADOR") or agent_mod.modelo_configurado()


def config_geracao(modelo: str) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=int(os.environ.get("MAX_TOKENS_SAIDA", "2048")),
        response_mime_type="application/json",
        thinking_config=agent_mod.config_pensamento(modelo),
    )


def criar_validador(modelo: str | None = None) -> LlmAgent:
    modelo = modelo or modelo_configurado()
    return LlmAgent(name=NOME, model=modelo, description="Revisa a resposta do Cabe no Bolso antes de ela chegar ao cliente.",
                    instruction=INSTRUCAO_FIXA, tools=[], generate_content_config=config_geracao(modelo))


def _runner(modelo: str) -> Runner:
    if modelo not in _RUNNERS:
        _RUNNERS[modelo] = Runner(app_name=APP, agent=criar_validador(modelo), session_service=InMemorySessionService())
    return _RUNNERS[modelo]


# ------------------------------------------------------------------ interpretação
def interpretar(bruto: str | dict | None) -> dict:
    """Normaliza a saída do validador: {aprovado, violacoes, orientacao, bloqueante, r8, corrigivel, json_valido}."""
    s = checagens.analisar_saida(bruto)
    if s is None or "aprovado" not in s:
        return {"aprovado": None, "violacoes": [], "orientacao": None, "bloqueante": False, "r8": False, "corrigivel": False, "json_valido": False}
    viol = []
    for v in s.get("violacoes") or []:
        if not isinstance(v, dict):
            continue
        regra = str(v.get("regra") or "").strip().upper()
        grav = str(v.get("gravidade") or "").strip().lower()
        if grav not in ("bloqueante", "corrigivel"):
            grav = "bloqueante" if regra in BLOQUEANTES else "corrigivel"
        viol.append({"regra": regra, "gravidade": grav, "trecho": str(v.get("trecho") or "")[:300], "motivo": str(v.get("motivo") or "")[:300]})
    aprovado = bool(s.get("aprovado")) and not viol
    return {"aprovado": aprovado, "violacoes": viol, "orientacao": s.get("orientacao_para_regenerar") or None,
            "bloqueante": any(v["gravidade"] == "bloqueante" for v in viol), "r8": any(v["regra"] == "R8" for v in viol),
            "corrigivel": bool(viol) and all(v["gravidade"] == "corrigivel" for v in viol), "json_valido": True}


def texto_correcao(resultado: dict, em_json: bool = True) -> str:
    """Mensagem que volta ao agente para a única regeneração: as violações e a orientação do validador.

    em_json=True (modo gi): a nova resposta vem no mesmo JSON. em_json=False (modo tools): texto corrido, sem JSON nem
    bloco de código (o embrulho em JSON é feito pelo runtime; um bloco JSON no texto reprova por R11)."""
    formato = "no mesmo formato JSON" if em_json else "em texto corrido (sem JSON, sem bloco de código, sem markdown)"
    linhas = [f"A resposta anterior foi reprovada pelo validador. Gere uma nova resposta, {formato}, corrigindo:"]
    for v in resultado.get("violacoes") or []:
        linhas.append(f"- {v['regra']} ({v['gravidade']}): {v['motivo']}" + (f' [trecho: "{v["trecho"][:120]}"]' if v.get("trecho") else ""))
    if resultado.get("orientacao"):
        linhas.append(f"Orientação: {resultado['orientacao']}")
    linhas.append("Cite só números que estão no contexto, exatamente como estão" + (", e liste todos em numeros_citados." if em_json else "."))
    return "\n".join(linhas)


# ------------------------------------------------------------------ chamada
async def validar_async(saida: dict | str, contexto: dict, historico: list[dict] | None = None, *, modelo: str | None = None,
                        diretrizes: str | None = None) -> dict:
    """Uma chamada ao validador. Devolve interpretar(...) + {erro, latencia_ms, tokens_entrada, tokens_saida, bruto, modelo}."""
    modelo = modelo or modelo_configurado()
    prompt = prompt_gi.prompt_validador(contexto, diretrizes, historico or [], saida)
    runner = _runner(modelo)
    sid = f"val-{uuid.uuid4().hex[:12]}"
    t0 = time.perf_counter()
    textos: list[str] = []
    tin = tout = 0
    try:
        await runner.session_service.create_session(app_name=APP, user_id="validador", session_id=sid, state={})
        async for ev in runner.run_async(user_id="validador", session_id=sid,
                                         new_message=types.Content(role="user", parts=[types.Part(text=prompt)])):
            um = getattr(ev, "usage_metadata", None)
            if um is not None:
                tin += int(getattr(um, "prompt_token_count", 0) or 0)
                tout += int(getattr(um, "candidates_token_count", 0) or 0)
            if ev.author == NOME and ev.content and ev.content.parts and not getattr(ev, "partial", False):
                for p in ev.content.parts:
                    if p.text and not getattr(p, "thought", False):
                        textos.append(p.text)
    except Exception as e:  # o validador nunca derruba a conversa
        return {"aprovado": None, "violacoes": [], "orientacao": None, "bloqueante": False, "r8": False, "corrigivel": False,
                "json_valido": False, "erro": f"{type(e).__name__}: {str(e)[:200]}", "latencia_ms": int((time.perf_counter() - t0) * 1000),
                "tokens_entrada": tin, "tokens_saida": tout, "bruto": "", "modelo": modelo}
    finally:
        try:
            await runner.session_service.delete_session(app_name=APP, user_id="validador", session_id=sid)
        except Exception:
            pass
    bruto = "\n".join(t for t in textos if t.strip()).strip()
    r = interpretar(bruto)
    r.update({"erro": None if r["json_valido"] else "saída do validador não é JSON", "latencia_ms": int((time.perf_counter() - t0) * 1000),
              "tokens_entrada": tin, "tokens_saida": tout, "bruto": bruto[:2000], "modelo": modelo})
    return r


def resumo(resultado: dict) -> str:
    if resultado.get("erro") and resultado.get("aprovado") is None:
        return f"validador indisponível: {resultado['erro']}"
    if resultado.get("aprovado"):
        return "aprovado"
    return "reprovado: " + "; ".join(f"{v['regra']} ({v['gravidade']}): {v['motivo'][:80]}" for v in resultado.get("violacoes") or []) \
        + (f" · orientação: {resultado['orientacao'][:120]}" if resultado.get("orientacao") else "")


def saida_json_de_texto(texto: str, acao: str = "nenhuma", oferta_id: str | None = None) -> dict:
    """Modo tools: embrulha o texto do agente no formato da Gi para o validador (mensagens, acao, oferta_id, numeros_citados)."""
    blocos = [b.strip() for b in (texto or "").replace("\r", "").split("\n\n") if b.strip()]
    if len(blocos) > 3:
        blocos = blocos[:2] + [" ".join(blocos[2:])]
    citados = []
    for n in checagens.extrair(" ".join(blocos)):
        if n["texto"] not in citados:
            citados.append(n["texto"])
    return {"mensagens": blocos, "acao": acao, "oferta_id": oferta_id, "numeros_citados": citados}


__all__ = ["ativo", "modelo_configurado", "criar_validador", "validar_async", "interpretar", "texto_correcao", "resumo",
           "saida_json_de_texto", "BLOQUEANTES", "CORRIGIVEIS", "NOME"]
