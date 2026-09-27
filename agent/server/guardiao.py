"""Guardião na borda da API: confere cada número do texto do agente contra numeros_validados e barra termos fora do plano.

É a mesma regra do after_model_callback do agente (docs/05, princípio 1), aplicada de novo antes de a resposta sair:
vale para o modo sem LLM (texto montado em código, conferido mesmo assim) e como segunda barreira para o modo com LLM.
Número sem origem vira "[valor sem origem]" e entra em `removidos`; termo da lista negra vira "[termo removido]".
"""
from __future__ import annotations

import re

from cabe_no_bolso import policy

MARCA_NUMERO = "[valor sem origem]"
MARCA_TERMO = "[termo removido]"

# Uma única alternância: dinheiro | percentual | mês/ano | data | inteiro solto. Não há sobreposição entre grupos.
_RE = re.compile(
    r"(?P<dinheiro>R\$\s?(?P<reais>\d{1,3}(?:\.\d{3})*|\d+)(?:,(?P<cents>\d{2}))?)"
    r"|(?P<pct>(?P<pct_num>\d{1,3}(?:,\d{1,2})?)%)"
    r"|(?P<mes>\b(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)/\d{4}\b)"
    r"|(?P<data>\b\d{1,2}/\d{1,2}/\d{4}\b)"
    r"|(?P<inteiro>\b\d+\b)"
)


def conjuntos(numeros: list[dict]) -> tuple[set[int], set[float]]:
    """Separa os números validados em inteiros (centavos, dias, parcelas...) e frações (taxas)."""
    ints: set[int] = set()
    floats: set[float] = set()
    for n in numeros or []:
        v = n.get("valor") if isinstance(n, dict) else None
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, int):
            ints.add(v)
        elif isinstance(v, float):
            if v.is_integer():
                ints.add(int(v))
            floats.add(v)
    return ints, floats


def conferir(texto: str, numeros: list[dict]) -> dict:
    """Devolve {texto, removidos: [str], termos_bloqueados: [str]}. Só os números presentes em `numeros` sobrevivem."""
    ints, floats = conjuntos(numeros)
    removidos: list[str] = []

    def valido_dinheiro(reais: str, cents: str | None) -> bool:
        r = int(reais.replace(".", ""))
        if cents is not None:
            return r * 100 + int(cents) in ints
        # frase arredondada (docs/06): vale se algum validado arredonda para esse valor em reais
        return any(round(v / 100) == r for v in ints)

    def valido_pct(num: str) -> bool:
        x = float(num.replace(",", "."))
        if x.is_integer() and int(x) in ints:  # composição da fatura: pct inteiro
            return True
        return any(abs(f * 100 - x) < 1e-6 for f in floats)

    def troca(m: re.Match) -> str:
        if m.group("mes") or m.group("data"):
            return m.group(0)
        ok = False
        if m.group("dinheiro"):
            ok = valido_dinheiro(m.group("reais"), m.group("cents"))
        elif m.group("pct"):
            ok = valido_pct(m.group("pct_num"))
        elif m.group("inteiro"):
            ok = int(m.group("inteiro")) in ints
        if ok:
            return m.group(0)
        removidos.append(m.group(0))
        return MARCA_NUMERO

    saida = _RE.sub(troca, texto or "")
    termos = policy.termos_bloqueados(saida)
    for termo in termos:
        saida = re.sub(re.escape(termo), MARCA_TERMO, saida, flags=re.IGNORECASE)
    return {"texto": saida, "removidos": removidos, "termos_bloqueados": termos}


def conferir_resposta(resposta: dict, numeros: list[dict]) -> dict:
    """Aplica `conferir` a cada mensagem do agente e aos textos dos cards; acumula em resposta['guardiao']."""
    g = resposta.setdefault("guardiao", {"removidos": [], "termos_bloqueados": []})
    g.setdefault("removidos", [])
    g.setdefault("termos_bloqueados", [])
    for m in resposta.get("mensagens") or []:
        if m.get("papel") != "agente":
            continue
        r = conferir(m.get("texto", ""), numeros)
        m["texto"] = r["texto"]
        g["removidos"] += r["removidos"]
        g["termos_bloqueados"] += r["termos_bloqueados"]
    for c in resposta.get("cards") or []:
        d = c.get("dados") or {}
        for campo in ("texto", "resumo", "aviso"):
            if isinstance(d.get(campo), str):
                r = conferir(d[campo], numeros)
                d[campo] = r["texto"]
                g["removidos"] += r["removidos"]
                g["termos_bloqueados"] += r["termos_bloqueados"]
    return resposta
