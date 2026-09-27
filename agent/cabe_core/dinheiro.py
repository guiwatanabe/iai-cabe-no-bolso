"""Dinheiro em centavos (int). Formatação só na borda. Marcação de origem de cada número."""
from __future__ import annotations

from typing import Any


def centavos(vlr: float | int | str) -> int:
    """Reais (float da base) -> centavos. Mesma regra de analise/persona_b.py: round(vlr * 100)."""
    return int(round(float(vlr) * 100))


def brl(c: int) -> str:
    """1234567 -> 'R$ 12.345,67'. Negativo vira '-R$ ...'."""
    c = int(c)
    sinal = "-" if c < 0 else ""
    reais, cents = divmod(abs(c), 100)
    inteiro = f"{reais:,}".replace(",", ".")
    return f"{sinal}R$ {inteiro},{cents:02d}"


def brl_arredondado(c: int) -> str:
    """Para frases: 'R$ 2.629' (cartões mostram centavos; frases arredondam, docs/06)."""
    c = int(c)
    sinal = "-" if c < 0 else ""
    reais = int(round(abs(c) / 100))
    return f"{sinal}R$ {reais:,}".replace(",", ".")


def pct(fracao: float, casas: int = 1) -> str:
    """0.035 -> '3,5%'."""
    s = f"{fracao * 100:.{casas}f}".replace(".", ",")
    return f"{s}%"


def pmt(valor: int, taxa_mes: float, n: int) -> int:
    """Parcela fixa (Price) em centavos para financiar `valor` a `taxa_mes` em n parcelas."""
    valor, n = int(valor), int(n)
    if n <= 0:
        raise ValueError("n deve ser positivo")
    if taxa_mes <= 0:
        return int(round(valor / n))
    fator = taxa_mes / (1 - (1 + taxa_mes) ** (-n))
    return int(round(valor * fator))


def num(valor: int, origem: str) -> dict:
    return {"valor": int(valor), "origem": origem}


def coletar_numeros(d: Any, prefixo: str, ignorar: tuple[str, ...] = ()) -> list[dict]:
    """Percorre um dict/list e devolve [{valor, origem}] para cada número (int/float, não bool).

    origem = 'prefixo:caminho.do.campo'. Chaves em `ignorar` são puladas (ex.: 'origem', 'numeros').
    Serve para acumular state['numeros_validados'] sem depender de cada função listar os campos à mão.
    """
    out: list[dict] = []

    def visita(x: Any, caminho: str) -> None:
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.append({"valor": x, "origem": f"{prefixo}:{caminho}"})
        elif isinstance(x, dict):
            for k, v in x.items():
                if k in ignorar:
                    continue
                visita(v, f"{caminho}.{k}" if caminho else str(k))
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                visita(v, f"{caminho}[{i}]")

    visita(d, "")
    return out


def mapa_origem(d: dict, prefixo: str, ignorar: tuple[str, ...] = ()) -> dict[str, str]:
    """{caminho.do.campo: 'prefixo:caminho.do.campo'} para os números de d (campo 'origem' dos retornos)."""
    return {n["origem"].split(":", 1)[1]: n["origem"] for n in coletar_numeros(d, prefixo, ignorar)}
