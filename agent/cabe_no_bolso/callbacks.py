"""Callbacks do agente: consentimento (before_tool), guardião (after_model), trace e FinOps (before_model, after_tool).

Assinaturas do google-adk 2.10 (conferidas no código do pacote):
  before_tool(tool, args, tool_context)  after_tool(tool, args, tool_context, tool_response)
  before_model(callback_context, llm_request)  after_model(callback_context, llm_response)

O guardião é a última barreira da regra 1 (docs/05): todo número que o cliente vê tem origem. Ele confere cada valor
em R$, percentual e contagem do texto do modelo contra state["numeros_validados"] (+ constantes de config/taxas.yaml),
remove a frase que traz número sem origem, troca "sujeito a" por "depende de aprovação" e barra a lista negra.
Também é aplicado pelo runtime sobre a resposta final, então vale para adk run, adk web e a API.

Dois guardrails vieram do repositório do Guilherme (padrão trazido de guiwatanabe/iai-cabe-no-bolso, `guardrails.py`):
- before_model `bloquear_entrada_insegura`: regex de injeção (PT e EN) sobre a última mensagem do usuário; se bater, o
  modelo nem é chamado e volta uma recusa fixa no formato do modo ativo (texto no modo tools, JSON no modo gi), com
  registro no trace. Quando before_model devolve resposta, o ADK não roda after_model (conferido em
  flows/llm_flows/core/_model_call.py), por isso o trace e o contador são gravados aqui mesmo, e o runtime pula o
  validador nesse turno (texto fixo em código, zero chamadas ao modelo).
- before_tool `limit_tool_calls`: teto de chamadas de ferramenta por turno (config/finops.yaml,
  travas.tool_calls_por_turno_max) no estado `temp:` do ADK, que vive só durante a invocação.
FinOps por papel: cada chamada ao modelo é somada em state["finops"]["chamadas_por_papel"][papel] (agente | regeneracao |
insight; o validador é somado pelo runtime), com chamadas, tokens de usage_metadata e latência.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from google.adk.models import LlmResponse
from google.genai import types

from cabe_core import finops as finops_core
from cabe_core.dinheiro import brl
from cabe_no_bolso import policy
from cabe_no_bolso.tools import FERRAMENTAS_DE_DADOS

CHAVE_TOOL_CALLS = "temp:tool_calls"   # prefixo temp: o ADK descarta ao fim da invocação => contador por turno
MENSAGEM_TETO_FERRAMENTAS = "Não consegui concluir a análise agora. Posso te mostrar as formas de pagar ou te passar para uma pessoa."
RECUSA_ENTRADA = ("Não posso seguir com esse pedido. Aqui eu cuido só da sua fatura e do que cabe no seu mês. "
                  "Quer ver as formas de pagar ou prefere falar com uma pessoa?")

# Papel da chamada ao modelo em curso, para o FinOps por papel (agente | regeneracao | insight; o validador é registrado
# pelo runtime). O runtime define antes de invocar o Runner (runtime._modelo); fora dele (adk web, adk run) vale "agente".
PAPEL_LLM: ContextVar[str] = ContextVar("papel_llm", default="agente")
PAPEIS_LLM = ("agente", "validador", "regeneracao", "insight")

# Injeção de prompt (entrada é dado, docs/05 princípio 10). Primeira linha de defesa, barata; PT e EN.
# Cada item: (rótulo que vai ao trace e ao log, padrão). O texto do cliente nunca vai ao log.
_INSTRUCOES = (r"(?:instru[çc][õo]es|instructions?|regras|rules|orienta[çc][õo]es|diretrizes|guidelines|prompts?|comandos|directives?)"
               r"(?:\s+(?:anteriores|acima|de\s+cima|iniciais|originais|previous|above|prior|earlier))?")
_QUALIFICADOR = (r"(?:(?:todas?|as|os|suas?|seus?|the|all|any|my|your|every|these|those|estas?|essas?|"
                 r"previous|prior|above|earlier|anteriores|acima|iniciais|originais|original|de\s+cima)\s+){0,3}")
PADROES_INJECAO: tuple[tuple[str, re.Pattern], ...] = (
    ("ignorar instruções", re.compile(r"\bignor\w*\s+" + _QUALIFICADOR + _INSTRUCOES + r"\b", re.IGNORECASE)),
    ("ignorar instruções", re.compile(r"\bignore\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|earlier)\b", re.IGNORECASE)),
    ("descartar instruções", re.compile(r"\b(?:esque[çc]a|esquece|esquecer|desconsidere|desconsidera|descarte|apague|forget|disregard|discard|override|overwrite)\s+"
                                        + _QUALIFICADOR + _INSTRUCOES + r"\b", re.IGNORECASE)),
    ("prompt de sistema", re.compile(r"\bsystem\s+(?:prompt|message|instructions?)\b|\bprompt\s+d[eo]\s+sistema\b|\b(?:instru[çc][õo]es|mensagem)\s+d[eo]\s+sistema\b",
                                     re.IGNORECASE)),
    ("revelar instruções", re.compile(r"\b(?:revel\w*|mostr\w*|repit\w*|repet\w*|exib\w*|imprim\w*|copi\w*|transcrev\w*|reproduz\w*|cite|diga|reveal|show|print|repeat|output|display|dump|leak|tell)\s+"
                                      r"(?:me\s+|-me\s+|pra\s+mim\s+|para\s+mim\s+)?" + _QUALIFICADOR
                                      + r"(?:instru[çc][õo]es|instructions?|prompt|regras|rules|diretrizes|guidelines|configura[çc][ãa]o|system\s+message)\b", re.IGNORECASE)),
    ("comando SQL", re.compile(r"\bdrop\s+table\b|\bdelete\s+from\b|\btruncate\s+table\b|\bunion\s+(?:all\s+)?select\b|\balter\s+table\b", re.IGNORECASE)),
    ("troca de papel", re.compile(r"\b(?:agora\s+voc[êe]\s+[ée]|a\s+partir\s+de\s+agora\s+voc[êe]\s+[ée]|you\s+are\s+now|from\s+now\s+on\s+you\s+are|"
                                  r"finja\s+(?:que\s+)?(?:[ée]|ser)|pretend\s+(?:you\s+are|to\s+be)|act\s+as\s+(?:a|an|if)\b|jailbreak|modo\s+desenvolvedor|"
                                  r"developer\s+mode|\bDAN\s+mode\b)", re.IGNORECASE)),
)

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

    paragrafos = [p for p in re.split(r"\n\s*\n", texto) if p.strip()]
    saidas_par: list[list[str]] = []
    flags = {"pediu": False, "fora": False}          # um único pedido de conferência / aviso de fora do plano por resposta
    for paragrafo in paragrafos:
        saida: list[str] = []
        saidas_par.append(saida)
        _guardiao_paragrafo(paragrafo, validos, relatorio, saida, flags)
    novo = "\n\n".join(" ".join(s.strip() for s in saida).strip() for saida in saidas_par if saida).strip() or RESPOSTA_VAZIA
    relatorio["alterado"] = novo != texto.strip() or bool(relatorio["removidos"]) or relatorio["substituicoes"] > 0
    return novo, relatorio


def _guardiao_paragrafo(texto: str, validos: list, relatorio: dict, saida: list[str], flags: dict) -> None:
    """Uma passada do guardião num parágrafo (uma bolha do chat); as frases removidas viram um único pedido de conferência."""
    frases = [f for f in _RE_FRASES.split(texto) if f is not None]
    pediu_confirmacao, fora_do_plano = flags["pediu"], flags["fora"]
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
    flags["pediu"], flags["fora"] = pediu_confirmacao, fora_do_plano


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
    """Consentimento antes de ler histórico (docs/05, princípio 3) e teto de ferramentas por turno (FinOps).

    Bloqueio em código, não por instrução. O teto por turno (config/finops.yaml: travas.tool_calls_por_turno_max) é o
    padrão `limit_tool_calls` trazido de guiwatanabe/iai-cabe-no-bolso (Guilherme): contador em state['temp:tool_calls'],
    que o ADK descarta ao fim da invocação; acima do teto a ferramenta não roda e o agente responde com o que já tem.
    """
    st = tool_context.state
    _INICIO_TOOL[_chave(tool_context)] = (time.perf_counter(), len(st.get("numeros_validados") or []))
    chamadas = int(st.get(CHAVE_TOOL_CALLS) or 0) + 1
    st[CHAVE_TOOL_CALLS] = chamadas
    teto = finops_core.teto_tool_calls_por_turno()
    if chamadas > teto:
        trace = list(st.get("trace") or [])
        trace.append({"ordem": len(trace) + 1, "ferramenta": tool.name, "argumentos": _args_sanitizados(args),
                      "resumo": f"bloqueada: teto de {teto} ferramentas por turno (config/finops.yaml)", "numeros": [], "duracao_ms": 0,
                      "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "llm": False, "bloqueada": True})
        st["trace"] = trace
        _log("ferramenta_bloqueada", sessao=_sessao_id(tool_context), ferramenta=tool.name, motivo="teto_tool_calls_por_turno", teto=teto)
        return {"bloqueado": True, "motivo": f"teto de {teto} chamadas de ferramenta por turno",
                "instrucao_ao_agente": "Não chame mais ferramentas neste turno. Responda com o que já tem, em uma frase curta, "
                                       "e ofereça as formas de pagar ou uma pessoa.",
                "mensagem_cliente": MENSAGEM_TETO_FERRAMENTAS}
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


# ------------------------------------------------------------------ entrada é dado: bloqueio de injeção antes do modelo
def texto_do_usuario(llm_request) -> str:
    """Texto da última mensagem de usuário do pedido (só partes de texto; respostas de ferramenta não contam)."""
    contents = getattr(llm_request, "contents", None) or []
    if not contents:
        return ""
    ultimo = contents[-1]
    if getattr(ultimo, "role", None) != "user":
        return ""
    return " ".join((p.text or "") for p in (getattr(ultimo, "parts", None) or [])
                    if getattr(p, "text", None) and not getattr(p, "function_response", None)).strip()


def detectar_injecao(texto: str) -> str | None:
    """Rótulo do primeiro padrão de injeção que bate no texto, ou None. Nunca devolve o texto (vai ao log e ao trace)."""
    if not texto:
        return None
    for rotulo, padrao in PADROES_INJECAO:
        if padrao.search(texto):
            return rotulo
    return None


def recusa_de_entrada(formato_json: bool) -> LlmResponse:
    """Resposta fixa no formato do modo ativo: JSON do prompt da Gi (modo gi) ou texto corrido (modo tools)."""
    if formato_json:
        texto = json.dumps({"mensagens": [RECUSA_ENTRADA], "acao": "nenhuma", "oferta_id": None, "numeros_citados": []}, ensure_ascii=False)
    else:
        texto = RECUSA_ENTRADA
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=texto)]))


def bloquear_entrada_insegura(callback_context, llm_request) -> LlmResponse | None:
    """before_model: se a última mensagem do usuário traz padrão de injeção, o modelo não é chamado e volta a recusa.

    Padrão `block_unsafe_input` trazido de guiwatanabe/iai-cabe-no-bolso (Guilherme), adaptado: regex em PT e EN,
    recusa no formato do modo (JSON no modo gi) e registro no trace ("como cheguei aqui") e no log estruturado.
    """
    rotulo = detectar_injecao(texto_do_usuario(llm_request))
    if not rotulo:
        return None
    st = callback_context.state
    cfg = getattr(llm_request, "config", None)
    em_json = getattr(cfg, "response_mime_type", None) == "application/json" or st.get("modo_conversa") == "gi"
    trace = list(st.get("trace") or [])
    trace.append({"ordem": len(trace) + 1, "etapa": "bloqueio_entrada", "ferramenta": "bloqueio_entrada",
                  "argumentos": {"padrao": rotulo, "formato": "json" if em_json else "texto"},
                  "resumo": f"entrada bloqueada antes do modelo: padrão de injeção ({rotulo}); recusa fixa em código, zero chamadas",
                  "numeros": [], "duracao_ms": 0, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "llm": False})
    st["trace"] = trace
    st["entradas_bloqueadas"] = int(st.get("entradas_bloqueadas") or 0) + 1
    _INICIO_MODELO.pop(callback_context.invocation_id, None)     # after_model não roda quando before_model responde
    _log("entrada_bloqueada", sessao=_sessao_id(callback_context), padrao=rotulo, formato="json" if em_json else "texto")
    return recusa_de_entrada(em_json)


def before_model(callback_context, llm_request) -> LlmResponse | None:
    """Cronômetro da chamada + bloqueio de injeção (a recusa volta sem chamar o modelo)."""
    _INICIO_MODELO[callback_context.invocation_id] = time.perf_counter()
    return bloquear_entrada_insegura(callback_context, llm_request)


def modelos_configurados() -> tuple[str, str]:
    """(modelo do agente, modelo do validador) por ambiente; import tardio para não criar ciclo com agent.py."""
    from cabe_no_bolso import agent as agent_mod  # noqa: WPS433
    m = agent_mod.modelo_configurado()
    return m, (os.environ.get("MODELO_VALIDADOR") or m)


def custo_corrente(f: dict) -> float | None:
    """Custo acumulado (USD) do bloco finops do state, agente + validador, com os preços de config/finops.yaml."""
    m, mv = modelos_configurados()
    return finops_core.custo_por_papel(f, m, mv)["total"]["custo_usd"]


def _finops(st, llm_response, latencia_ms: int | None) -> None:
    f = dict(st.get("finops") or {"chamadas_llm": 0, "tokens_entrada": 0, "tokens_saida": 0, "latencias_ms": [], "custo_estimado": None})
    um = getattr(llm_response, "usage_metadata", None)
    f["chamadas_llm"] = int(f.get("chamadas_llm", 0)) + 1
    if um is not None:
        f["tokens_entrada"] = int(f.get("tokens_entrada", 0)) + int(getattr(um, "prompt_token_count", 0) or 0)
        f["tokens_saida"] = int(f.get("tokens_saida", 0)) + int(getattr(um, "candidates_token_count", 0) or 0)
    if latencia_ms is not None:
        f["latencias_ms"] = list(f.get("latencias_ms") or []) + [latencia_ms]
    somar_por_papel(f, PAPEL_LLM.get(), um, latencia_ms, modelos_configurados()[0])
    f["custo_estimado"] = custo_corrente(f)   # USD pelo preço com fonte em config/finops.yaml (None só se o preço não estiver confirmado)
    st["finops"] = f


def somar_por_papel(f: dict, papel: str, usage_metadata, latencia_ms: int | None, modelo: str | None = None) -> None:
    """Soma uma chamada ao modelo em f["chamadas_por_papel"][papel] (agente | validador | regeneracao | insight):
    chamadas, tokens de usage_metadata (prompt_token_count / candidates_token_count), latências e modelo. Muta f."""
    pp = dict(f.get("chamadas_por_papel") or {})
    reg = dict(pp.get(papel) or {"chamadas": 0, "tokens_entrada": 0, "tokens_saida": 0, "latencias_ms": []})
    reg["chamadas"] = int(reg.get("chamadas", 0)) + 1
    if usage_metadata is not None:
        reg["tokens_entrada"] = int(reg.get("tokens_entrada", 0)) + int(getattr(usage_metadata, "prompt_token_count", 0) or 0)
        reg["tokens_saida"] = int(reg.get("tokens_saida", 0)) + int(getattr(usage_metadata, "candidates_token_count", 0) or 0)
    if latencia_ms is not None:
        reg["latencias_ms"] = list(reg.get("latencias_ms") or []) + [latencia_ms]
    if modelo:
        reg["modelo"] = modelo
    pp[papel] = reg
    f["chamadas_por_papel"] = pp


def after_model(callback_context, llm_response):
    """Guardião (docs/05, princípio 1, 6 e 9) + métricas FinOps. Devolve LlmResponse alterada ou None."""
    st = callback_context.state
    inicio = _INICIO_MODELO.pop(callback_context.invocation_id, None)
    latencia = int(round((time.perf_counter() - inicio) * 1000)) if inicio else None
    if getattr(llm_response, "partial", False):
        return None
    _finops(st, llm_response, latencia)
    tem_chamada = bool(llm_response.content and any(p.function_call for p in (llm_response.content.parts or [])))
    um = getattr(llm_response, "usage_metadata", None)
    tin, tout = int(getattr(um, "prompt_token_count", 0) or 0), int(getattr(um, "candidates_token_count", 0) or 0)
    modelo_ag = modelos_configurados()[0]
    _log("modelo", sessao=_sessao_id(callback_context), papel=PAPEL_LLM.get(), modelo=modelo_ag, latencia_ms=latencia, chamada_de_ferramenta=tem_chamada,
         tokens_entrada=tin, tokens_saida=tout, custo_usd=finops_core.custo_usd(tin, tout, modelo_ag)["custo_usd"])

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
           "p50_p95", "MENSAGEM_SEM_CONSENTIMENTO", "PEDIDO_DE_CONFIRMACAO", "FORA_DO_PLANO", "TERMOS_LISTA_NEGRA", "brl",
           "bloquear_entrada_insegura", "detectar_injecao", "recusa_de_entrada", "texto_do_usuario", "PADROES_INJECAO", "RECUSA_ENTRADA",
           "PAPEL_LLM", "PAPEIS_LLM", "somar_por_papel", "CHAVE_TOOL_CALLS", "MENSAGEM_TETO_FERRAMENTAS"]
