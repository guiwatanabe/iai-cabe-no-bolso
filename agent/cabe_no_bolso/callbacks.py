"""Callbacks do agente: consentimento (before_tool), guardião (after_model), trace e FinOps (before_model, after_tool).

Assinaturas do google-adk 2.10 (conferidas no código do pacote):
  before_tool(tool, args, tool_context)  after_tool(tool, args, tool_context, tool_response)
  before_model(callback_context, llm_request)  after_model(callback_context, llm_response)

O guardião é a última barreira da regra 1 (docs/05): todo número que o cliente vê tem origem. Ele confere cada valor
em R$, percentual e contagem do texto do modelo contra state["numeros_validados"] (+ constantes de config/taxas.yaml),
remove a frase que traz número sem origem, troca "sujeito a" por "depende de aprovação" e barra a lista negra.
Também é aplicado pelo runtime sobre a resposta final, então vale para adk run, adk web e a API.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from google.genai import types

from cabe_core.dinheiro import brl
from cabe_no_bolso import policy
from cabe_no_bolso.tools import FERRAMENTAS_DE_DADOS

MENSAGEM_SEM_CONSENTIMENTO = ("Para olhar seu extrato eu preciso da sua permissão. "
                              "Posso ver seus últimos 90 dias de conta e cartão? Você pode desligar quando quiser.")
PEDIDO_DE_CONFIRMACAO = "Um dos valores ainda precisa de conferência antes de eu te passar. Quer que eu confira e volte com o número certo?"
FORA_DO_PLANO = "Isso não faz parte do que eu faço aqui: cuido só da fatura deste mês e do que cabe no seu bolso."
RESPOSTA_VAZIA = "Vamos olhar o que cabe no seu mês. Quer ver as opções ou prefere falar com uma pessoa?"

TERMOS_PRODUTO = ("seguro", "seguros", "prestamista", "cashback", "pontos", "cartão novo", "cartao novo", "novo cartão", "novo cartao",
                  "investimento", "investimentos", "investir", "capitalização", "capitalizacao")
TERMOS_JULGAMENTO = ("gasta demais", "gastou demais", "gastando demais", "deveria", "devia", "descontrole", "descontrolad",
                     "erro seu", "sua culpa", "irresponsá", "falta de planejamento", "educação financeira")
TERMOS_CONDICAO = ("sujeito a", "sujeita a", "sujeitos a", "sujeitas a")
TERMOS_LISTA_NEGRA = TERMOS_PRODUTO + TERMOS_JULGAMENTO + TERMOS_CONDICAO
_RE_SUJEITO = re.compile(
    r"(?:(?:est[áa]|est[ãa]o|[ée]|s[ãa]o|fica|ficam)\s+)?sujeit(?P<num>[oa]s?)\s+[àa]\s*"
    r"(?:an[áa]lise(?:\s+de\s+cr[ée]dito)?|aprova[çc][ãa]o(?:\s+de\s+cr[ée]dito)?|avalia[çc][ãa]o|cr[ée]dito|disponibilidade|confirma[çc][ãa]o)?",
    re.IGNORECASE)

_RE_REAIS = re.compile(r"R\$\s?(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?")
_RE_PCT = re.compile(r"(\d+(?:[,.]\d+)?)\s?%")
_RE_CONTAGEM = re.compile(r"\b(\d{1,3}(?:,\d{1,2})?)\s?(?:x\b|×|parcelas?\b|vezes\b|dias?\b|meses\b|faturas?\b)", re.IGNORECASE)
_RE_DIA = re.compile(r"\bdia\s+(\d{1,2})\b", re.IGNORECASE)
_RE_FRASES = re.compile(r"(?<=[.!?])\s+|\n+")

_INICIO_TOOL: dict[Any, tuple[float, int]] = {}
_INICIO_MODELO: dict[Any, float] = {}


# ------------------------------------------------------------------ números de referência
@lru_cache(maxsize=1)
def numeros_config() -> list[dict]:
    """Constantes de config/taxas.yaml que o agente pode citar (com origem 'config:taxas.yaml:<chave>')."""
    t = policy.registro_taxas()
    pares = [
        ("rotativo.taxa_mes", t["rotativo"]["taxa_mes"]),
        ("rotativo.minimo_pct_fatura", t["rotativo"].get("minimo_pct_fatura", 0.15)),
        ("rotativo.teto_encargos_pct", t["rotativo"].get("teto_encargos_pct", 1.0)),
        ("janela_meses", t.get("janela_meses", 3)),
        ("regras_plano.faturas_inteiras_para_encerrar", t.get("regras_plano", {}).get("faturas_inteiras_para_encerrar", 3)),
        ("cobertura_curta.teto_dias", t.get("cobertura_curta", {}).get("teto_dias", 25)),
        ("cobertura_curta.ampliacao_limite_pct", t.get("cobertura_curta", {}).get("ampliacao_limite_pct", 0.10)),
        ("parcelamento.vezes_por_12_meses", t.get("parcelamento", {}).get("vezes_por_12_meses", 1)),
        ("janela_dias", 90),
        ("historico_meses", 12),
    ]
    out = [{"valor": v, "origem": f"config:taxas.yaml:{k}"} for k, v in pares if isinstance(v, (int, float)) and not isinstance(v, bool)]
    for nome in ("cheque_especial", "parcelamento_fatura", "credito_pessoal", "consignado_clt", "consignado_inss"):
        bloco = t.get(nome) or {}
        taxa = bloco.get("taxa_mes") or bloco.get("taxa_mes_base")
        if taxa:
            out.append({"valor": float(taxa), "origem": f"config:taxas.yaml:{nome}.taxa_mes"})
    return out


def _centavos(inteiro: str, cents: str | None) -> tuple[int, bool]:
    reais = int(inteiro.replace(".", ""))
    if cents is None:
        return reais * 100, False
    c = int(cents) if len(cents) == 2 else int(cents) * 10
    return reais * 100 + c, True


def _valido_reais(c: int, com_centavos: bool, valores: list) -> bool:
    """c é sempre positivo (o sinal fica fora do 'R$'); um valor negativo do núcleo vale pelo módulo ('-R$ 773,57')."""
    for v in valores:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        va = abs(v)
        if abs(va - c) <= 1:
            return True
        if not com_centavos and isinstance(v, int) and round(va / 100) * 100 == c:   # frase arredondada (docs/06)
            return True
    return False


def _valido_pct(p: float, valores: list) -> bool:
    for v in valores:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if abs(float(v) - p) < 0.0005 or abs(float(v) - p * 100) < 0.5:
            return True
    return False


def _valido_float(x: float, valores: list) -> bool:
    return any(not isinstance(v, bool) and isinstance(v, (int, float)) and abs(float(v) - x) < 0.05 for v in valores)


def _valido_int(n: int, valores: list) -> bool:
    return any(isinstance(v, int) and not isinstance(v, bool) and v == n for v in valores)


def numeros_da_frase(frase: str) -> list[dict]:
    """Extrai os números que o cliente veria numa frase: R$, %, contagens (10 parcelas, 7 dias, 3x) e 'dia N'."""
    out = []
    for m in _RE_REAIS.finditer(frase):
        c, com = _centavos(m.group(1), m.group(2))
        out.append({"texto": m.group(0), "tipo": "reais", "valor": c, "com_centavos": com})
    sem_reais = _RE_REAIS.sub(" ", frase)
    for m in _RE_PCT.finditer(sem_reais):
        out.append({"texto": m.group(0), "tipo": "pct", "valor": float(m.group(1).replace(",", ".")) / 100})
    for m in _RE_CONTAGEM.finditer(sem_reais):
        bruto = m.group(1)
        if "," in bruto:
            out.append({"texto": m.group(0), "tipo": "float", "valor": float(bruto.replace(",", "."))})
        else:
            out.append({"texto": m.group(0), "tipo": "int", "valor": int(bruto)})
    for m in _RE_DIA.finditer(sem_reais):
        out.append({"texto": m.group(0), "tipo": "int", "valor": int(m.group(1))})
    return out


def guardiao_texto(texto: str, numeros_validados: list[dict] | None) -> tuple[str, dict]:
    """Aplica o guardião a um texto. Devolve (texto_corrigido, relatorio).

    relatorio = {removidos: [{frase, motivo, numeros}], termos_bloqueados: [str], substituicoes: int, alterado: bool}
    """
    validos = [n["valor"] for n in (numeros_validados or [])] + [n["valor"] for n in numeros_config()]
    relatorio = {"removidos": [], "termos_bloqueados": [], "substituicoes": 0, "alterado": False}
    if not texto or not texto.strip():
        return texto, relatorio

    frases = [f for f in _RE_FRASES.split(texto) if f is not None]
    saida: list[str] = []
    pediu_confirmacao = fora_do_plano = False
    for frase in frases:
        if not frase.strip():
            continue
        f = frase
        if _RE_SUJEITO.search(f):
            def _troca(m):
                return "dependem de aprovação" if m.group("num").lower().endswith("s") else "depende de aprovação"
            f, n = _RE_SUJEITO.subn(_troca, f)
            f = f[:1].upper() + f[1:]
            relatorio["termos_bloqueados"].append("sujeito a")
            relatorio["substituicoes"] += n
        baixo = f.lower()
        produto = next((t for t in TERMOS_PRODUTO if re.search(r"\b" + re.escape(t) + r"\b", baixo)), None)
        if produto:
            relatorio["termos_bloqueados"].append(produto)
            relatorio["removidos"].append({"frase": frase, "motivo": f"termo fora do plano: {produto}", "numeros": []})
            if not fora_do_plano:
                saida.append(FORA_DO_PLANO)
                fora_do_plano = True
            continue
        julg = next((t for t in TERMOS_JULGAMENTO if t in baixo), None)
        if julg:
            relatorio["termos_bloqueados"].append(julg)
            relatorio["removidos"].append({"frase": frase, "motivo": f"julgamento: {julg}", "numeros": []})
            continue
        sem_origem = []
        for n in numeros_da_frase(f):
            ok = (_valido_reais(n["valor"], n["com_centavos"], validos) if n["tipo"] == "reais"
                  else _valido_pct(n["valor"], validos) if n["tipo"] == "pct"
                  else _valido_float(n["valor"], validos) if n["tipo"] == "float"
                  else _valido_int(n["valor"], validos))
            if not ok:
                sem_origem.append(n["texto"])
        if sem_origem:
            relatorio["removidos"].append({"frase": frase, "motivo": "número sem origem", "numeros": sem_origem})
            if not pediu_confirmacao:
                saida.append(PEDIDO_DE_CONFIRMACAO)
                pediu_confirmacao = True
            continue
        saida.append(f)

    novo = " ".join(s.strip() for s in saida).strip() or RESPOSTA_VAZIA
    relatorio["alterado"] = novo != texto.strip() or bool(relatorio["removidos"]) or relatorio["substituicoes"] > 0
    return novo, relatorio


# ------------------------------------------------------------------ callbacks do ADK
def _log(evento: str, **campos: Any) -> None:
    """Log JSON estruturado no stdout (Cloud Logging). Só IDs, nomes e métricas: nunca conteúdo do extrato."""
    reg = {"severity": "INFO", "evento": evento, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **campos}
    print(json.dumps(reg, ensure_ascii=False, default=str), file=sys.stdout, flush=True)


def _sessao_id(ctx) -> str | None:
    try:
        return ctx.session.id
    except Exception:
        return None


def _chave(ctx) -> Any:
    return getattr(ctx, "function_call_id", None) or id(ctx)


def _args_sanitizados(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if k == "tool_context":
            continue
        if k == "cliente_id" and isinstance(v, str) and len(v) > 8:
            v = v[:8] + "…"
        out[k] = v
    return out


def _resumo_resposta(nome: str, resp: dict) -> str:
    if not isinstance(resp, dict):
        return f"{nome} concluída"
    if resp.get("bloqueado"):
        return f"{nome} bloqueada: {resp.get('motivo')}"
    if resp.get("erro"):
        return f"{nome}: {resp['erro']}"
    for k in ("frase", "motivo", "texto_sugerido", "proximo_passo"):
        if resp.get(k):
            return str(resp[k])
    if nome == "detalhar_fatura":
        return f"compras de {resp.get('compras_de')}: {resp.get('total_compras_no_cartao')}"
    if nome == "simular_continuar_no_rotativo":
        return f"{resp.get('sobre')} por {resp.get('meses')} mês(es): custo {resp.get('custo')}"
    if nome == "confirmar_plano":
        return f"plano {resp.get('caminho')}: parcela {resp.get('parcela')}, termina em {resp.get('termina_em')}"
    return f"{nome} concluída"


def before_tool(tool, args: dict, tool_context) -> dict | None:
    """Consentimento antes de ler histórico (docs/05, princípio 3). Bloqueio em código, não por instrução."""
    st = tool_context.state
    _INICIO_TOOL[_chave(tool_context)] = (time.perf_counter(), len(st.get("numeros_validados") or []))
    if tool.name in FERRAMENTAS_DE_DADOS and not st.get("consentimento"):
        trace = list(st.get("trace") or [])
        trace.append({"ordem": len(trace) + 1, "ferramenta": tool.name, "argumentos": _args_sanitizados(args),
                      "resumo": "bloqueada: sem consentimento registrado", "numeros": [], "duracao_ms": 0,
                      "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "llm": False, "bloqueada": True})
        st["trace"] = trace
        _log("ferramenta_bloqueada", sessao=_sessao_id(tool_context), ferramenta=tool.name, motivo="sem_consentimento")
        return {"bloqueado": True, "motivo": "sem consentimento registrado",
                "instrucao_ao_agente": "Não leia nada. Peça permissão com uma frase curta e espere o sim. "
                                       "Se o cliente já disse sim nesta conversa, chame registrar_consentimento(true) primeiro.",
                "mensagem_cliente": MENSAGEM_SEM_CONSENTIMENTO}
    return None


def after_tool(tool, args: dict, tool_context, tool_response: dict) -> dict | None:
    """Trace do painel 'como cheguei aqui' + log estruturado. Não altera a resposta da ferramenta."""
    st = tool_context.state
    inicio, n_antes = _INICIO_TOOL.pop(_chave(tool_context), (None, 0))
    if isinstance(tool_response, dict) and tool_response.get("bloqueado"):
        return None                                   # before_tool já registrou o bloqueio no trace
    duracao = int(round((time.perf_counter() - inicio) * 1000)) if inicio else None
    numeros = list(st.get("numeros_validados") or [])[n_antes:]
    trace = list(st.get("trace") or [])
    trace.append({"ordem": len(trace) + 1, "ferramenta": tool.name, "argumentos": _args_sanitizados(args),
                  "resumo": _resumo_resposta(tool.name, tool_response), "numeros": numeros, "duracao_ms": duracao,
                  "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "llm": False})
    st["trace"] = trace
    _log("ferramenta", sessao=_sessao_id(tool_context), ferramenta=tool.name, duracao_ms=duracao, numeros_novos=len(numeros),
         erro=bool(isinstance(tool_response, dict) and tool_response.get("erro")))
    return None


def before_model(callback_context, llm_request) -> None:
    _INICIO_MODELO[callback_context.invocation_id] = time.perf_counter()
    return None


def _finops(st, llm_response, latencia_ms: int | None) -> None:
    f = dict(st.get("finops") or {"chamadas_llm": 0, "tokens_entrada": 0, "tokens_saida": 0, "latencias_ms": [], "custo_estimado": None})
    um = getattr(llm_response, "usage_metadata", None)
    f["chamadas_llm"] = int(f.get("chamadas_llm", 0)) + 1
    if um is not None:
        f["tokens_entrada"] = int(f.get("tokens_entrada", 0)) + int(getattr(um, "prompt_token_count", 0) or 0)
        f["tokens_saida"] = int(f.get("tokens_saida", 0)) + int(getattr(um, "candidates_token_count", 0) or 0)
    if latencia_ms is not None:
        f["latencias_ms"] = list(f.get("latencias_ms") or []) + [latencia_ms]
    f["custo_estimado"] = None if policy.registro_taxas().get("preco_modelo") is None else f.get("custo_estimado")
    st["finops"] = f


def after_model(callback_context, llm_response):
    """Guardião (docs/05, princípio 1, 6 e 9) + métricas FinOps. Devolve LlmResponse alterada ou None."""
    st = callback_context.state
    inicio = _INICIO_MODELO.pop(callback_context.invocation_id, None)
    latencia = int(round((time.perf_counter() - inicio) * 1000)) if inicio else None
    if getattr(llm_response, "partial", False):
        return None
    _finops(st, llm_response, latencia)
    tem_chamada = bool(llm_response.content and any(p.function_call for p in (llm_response.content.parts or [])))
    _log("modelo", sessao=_sessao_id(callback_context), latencia_ms=latencia, chamada_de_ferramenta=tem_chamada,
         tokens_entrada=int(getattr(getattr(llm_response, "usage_metadata", None), "prompt_token_count", 0) or 0),
         tokens_saida=int(getattr(getattr(llm_response, "usage_metadata", None), "candidates_token_count", 0) or 0))

    chamadas = [p.function_call.name for p in ((llm_response.content.parts if llm_response.content else None) or []) if p.function_call]
    n_texto = sum(len(p.text or "") for p in ((llm_response.content.parts if llm_response.content else None) or []) if p.text)
    trace = list(st.get("trace") or [])
    trace.append({"ordem": len(trace) + 1, "etapa": "modelo", "ferramenta": "modelo", "argumentos": {"modelo": getattr(llm_response, "model_version", None)},
                  "resumo": (f"pediu {', '.join(chamadas)}" if chamadas else f"resposta em texto ({n_texto} caracteres)"), "numeros": [],
                  "duracao_ms": latencia, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "llm": True})
    st["trace"] = trace
    if not llm_response.content or not llm_response.content.parts:
        return None
    validados = st.get("numeros_validados") or []
    relatorio_total = {"removidos": [], "termos_bloqueados": [], "substituicoes": 0}
    alterado = False
    novas_partes = []
    for p in llm_response.content.parts:
        if p.text and not p.function_call and not p.function_response and not getattr(p, "thought", False):
            novo, rel = guardiao_texto(p.text, validados)
            relatorio_total["removidos"] += rel["removidos"]
            relatorio_total["termos_bloqueados"] += rel["termos_bloqueados"]
            relatorio_total["substituicoes"] += rel["substituicoes"]
            if rel["alterado"] and novo != p.text:
                alterado = True
                novas_partes.append(types.Part(text=novo))
                continue
        novas_partes.append(p)
    acumulado = dict(st.get("guardiao") or {"removidos": [], "termos_bloqueados": [], "substituicoes": 0})
    acumulado["removidos"] = list(acumulado.get("removidos") or []) + relatorio_total["removidos"]
    acumulado["termos_bloqueados"] = list(acumulado.get("termos_bloqueados") or []) + relatorio_total["termos_bloqueados"]
    acumulado["substituicoes"] = int(acumulado.get("substituicoes") or 0) + relatorio_total["substituicoes"]
    st["guardiao"] = acumulado
    if relatorio_total["removidos"] or relatorio_total["substituicoes"]:
        _log("guardiao", sessao=_sessao_id(callback_context), removidos=len(relatorio_total["removidos"]),
             termos=relatorio_total["termos_bloqueados"], substituicoes=relatorio_total["substituicoes"])
    if not alterado:
        return None
    return llm_response.model_copy(update={"content": types.Content(role=llm_response.content.role or "model", parts=novas_partes)})


def p50_p95(latencias: list[int]) -> tuple[int | None, int | None]:
    if not latencias:
        return None, None
    s = sorted(latencias)
    return s[int(0.5 * (len(s) - 1))], s[int(round(0.95 * (len(s) - 1)))]


__all__ = ["before_tool", "after_tool", "before_model", "after_model", "guardiao_texto", "numeros_da_frase", "numeros_config",
           "p50_p95", "MENSAGEM_SEM_CONSENTIMENTO", "PEDIDO_DE_CONFIRMACAO", "FORA_DO_PLANO", "TERMOS_LISTA_NEGRA", "brl"]
