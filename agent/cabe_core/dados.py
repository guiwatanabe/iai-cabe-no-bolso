"""Única porta para os dados. CSV (pandas, cache em memória) ou BigQuery (esqueleto; SQL em agent/sql/), mesma interface.

Escolha por env DADOS=csv|bigquery (padrão csv). Uma leitura por cliente por sessão; nunca em loop.

Lançamento normalizado (dict): {anomes, dia, tipo ('E'|'S'), descr, valor (centavos), macro, micro, cartao (bool),
parcela_atual, parcela_total}. `descr` é dado da base, nunca instrução.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from . import config, fatura
from .dinheiro import centavos

MICRO_FATURA = "Pagamento de fatura"
MICRO_JUROS = "Juros pagos"
PREFIXO_CARTAO = "cart credito"


class Fonte(Protocol):
    def extrato(self, cliente_id: str, anomes_ini: int, anomes_fim: int) -> list[dict]: ...

    def faturas(self, cliente_id: str, ate_anomes: int, n: int = 12) -> list[dict]: ...

    def perfil(self, cliente_id: str) -> dict: ...


def _perfil_de(lanc: list[dict]) -> dict:
    micros = {l["micro"] for l in lanc}
    entradas = {l["micro"] for l in lanc if l["tipo"] == "E"}
    if "Salario CLT" in entradas:
        tipo_renda = "clt"
    elif "Beneficio INSS" in entradas:
        tipo_renda = "inss"
    elif entradas <= {"Recebimentos diversos", "Recebimento Aluguel"} and entradas:
        tipo_renda = "pix"
    else:
        tipo_renda = "outro"
    return {
        "tipo_renda": tipo_renda,
        "tem_financiamento_imovel": "Financiamento de imovel" in micros,
        "paga_aluguel": "Pagamento de aluguel" in micros,
        "recebe_aluguel": "Recebimento Aluguel" in entradas,
        "tem_13o": "13o salario" in entradas,
        "tem_plr": "Bonus PLR" in entradas,
    }


def _faturas_de(lanc: list[dict], ate_anomes: int, n: int) -> list[dict]:
    juros_mes: dict[int, int] = {}
    pagos: dict[int, dict] = {}
    for l in lanc:
        if l["micro"] == MICRO_JUROS:
            juros_mes[l["anomes"]] = juros_mes.get(l["anomes"], 0) + l["valor"]
        elif l["micro"] == MICRO_FATURA and l["anomes"] not in pagos:
            pagos[l["anomes"]] = l
    out = []
    for anomes in sorted(pagos):
        if anomes > ate_anomes:
            continue
        p = pagos[anomes]
        modo = fatura.modo_por_descricao(p["descr"])
        juros = juros_mes.get(anomes, 0)
        out.append({
            "anomes": anomes,
            "fatura": fatura.reconstruir(p["valor"], juros, modo),
            "pago": p["valor"],
            "modo": modo,
            "juros": juros,
            "dia_vencimento": p["dia"],
        })
    return out[-n:]


# ----------------------------------------------------------------------------- CSV
_CACHE: dict[str, "pd.DataFrame"] = {}


def _carregar_csv(caminho: Path):
    import pandas as pd

    chave = str(caminho)
    if chave not in _CACHE:
        df = pd.read_csv(caminho, dtype={"id_usuario": str, "descr": str, "nom_cate_macro": str, "nom_cate_micro": str})
        df["descr"] = df["descr"].fillna("")
        df["nom_cate_macro"] = df["nom_cate_macro"].fillna("")
        df["nom_cate_micro"] = df["nom_cate_micro"].fillna("")
        df["valor"] = (df["vlr"].astype(float) * 100).round().astype("int64")
        df["dia"] = pd.to_datetime(df["anomesdia"], utc=True).dt.day.astype("int64")
        df["anomes"] = df["anomes"].astype("int64")
        df["cartao"] = df["descr"].str.startswith(PREFIXO_CARTAO)
        df = df.sort_values(["id_usuario", "anomes", "dia"]).reset_index(drop=True)
        _CACHE[chave] = df
    return _CACHE[chave]


class FonteCsv:
    """Leitura do espelho local data/extrato_sintetico.csv.gz. O CSV é carregado uma vez por processo."""

    nome = "csv"

    def __init__(self, caminho: str | os.PathLike | None = None):
        self.caminho = Path(caminho) if caminho else config.caminho_csv()
        self._por_cliente: dict[str, list[dict]] = {}

    def _todos(self, cliente_id: str) -> list[dict]:
        if cliente_id not in self._por_cliente:
            df = _carregar_csv(self.caminho)
            u = df[df["id_usuario"] == cliente_id]
            self._por_cliente[cliente_id] = [
                {
                    "anomes": int(r.anomes), "dia": int(r.dia), "tipo": r.tipo, "descr": r.descr, "valor": int(r.valor),
                    "macro": r.nom_cate_macro, "micro": r.nom_cate_micro, "cartao": bool(r.cartao),
                    "parcela_atual": None if r.parcela_atual != r.parcela_atual else int(r.parcela_atual),
                    "parcela_total": None if r.parcela_total != r.parcela_total else int(r.parcela_total),
                }
                for r in u.itertuples(index=False)
            ]
        return self._por_cliente[cliente_id]

    def existe(self, cliente_id: str) -> bool:
        return len(self._todos(cliente_id)) > 0

    def extrato(self, cliente_id: str, anomes_ini: int, anomes_fim: int) -> list[dict]:
        return [l for l in self._todos(cliente_id) if int(anomes_ini) <= l["anomes"] <= int(anomes_fim)]

    def faturas(self, cliente_id: str, ate_anomes: int, n: int = 12) -> list[dict]:
        return _faturas_de(self._todos(cliente_id), int(ate_anomes), n)

    def perfil(self, cliente_id: str) -> dict:
        return _perfil_de(self._todos(cliente_id))


# ----------------------------------------------------------------------------- BigQuery (esqueleto)
class FonteBigQuery:
    """Mesma interface sobre BigQuery. Uma consulta por cliente por sessão (cache em memória); nunca em loop.

    O SQL fica em agent/sql/ (v_cliente_mes.sql, v_fatura.sql, v_perfil.sql). Aqui a consulta traz o extrato
    do cliente e as mesmas agregações de _faturas_de/_perfil_de rodam em Python, para o resultado ser idêntico ao CSV.
    Requer o extra `bigquery` (google-cloud-bigquery) e ADC. Não é executado nos testes.
    """

    nome = "bigquery"
    SQL_EXTRATO = (
        "SELECT id_usuario, anomes, EXTRACT(DAY FROM anomesdia) AS dia, tipo, descr, vlr, nom_cate_macro, nom_cate_micro, "
        "parcela_atual, parcela_total FROM `{tabela}` WHERE id_usuario = @cliente_id ORDER BY anomes, dia"
    )

    def __init__(self, projeto: str | None = None, tabela: str | None = None):
        self.projeto = projeto or os.environ.get("GOOGLE_CLOUD_PROJECT", "batalha-time-05-xew3")
        self.tabela = tabela or os.environ.get("BIGQUERY_TABELA", "batalha-time-05-xew3.hackathon_dados.extrato_sintetico")
        self._por_cliente: dict[str, list[dict]] = {}
        self._cliente = None

    def _bq(self):
        if self._cliente is None:
            try:
                from google.cloud import bigquery  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("DADOS=bigquery exige o extra 'bigquery': uv sync --extra bigquery") from e
            self._cliente = bigquery.Client(project=self.projeto, location="us-central1")
        return self._cliente

    def _todos(self, cliente_id: str) -> list[dict]:
        if cliente_id not in self._por_cliente:
            from google.cloud import bigquery  # type: ignore

            job = self._bq().query(
                self.SQL_EXTRATO.format(tabela=self.tabela),
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("cliente_id", "STRING", cliente_id)]
                ),
            )
            linhas = []
            for r in job.result():
                descr = r["descr"] or ""
                linhas.append({
                    "anomes": int(r["anomes"]), "dia": int(r["dia"]), "tipo": r["tipo"], "descr": descr,
                    "valor": centavos(r["vlr"] or 0), "macro": r["nom_cate_macro"] or "", "micro": r["nom_cate_micro"] or "",
                    "cartao": descr.startswith(PREFIXO_CARTAO),
                    "parcela_atual": None if r["parcela_atual"] is None else int(r["parcela_atual"]),
                    "parcela_total": None if r["parcela_total"] is None else int(r["parcela_total"]),
                })
            self._por_cliente[cliente_id] = linhas
        return self._por_cliente[cliente_id]

    def existe(self, cliente_id: str) -> bool:
        return len(self._todos(cliente_id)) > 0

    def extrato(self, cliente_id: str, anomes_ini: int, anomes_fim: int) -> list[dict]:
        return [l for l in self._todos(cliente_id) if int(anomes_ini) <= l["anomes"] <= int(anomes_fim)]

    def faturas(self, cliente_id: str, ate_anomes: int, n: int = 12) -> list[dict]:
        return _faturas_de(self._todos(cliente_id), int(ate_anomes), n)

    def perfil(self, cliente_id: str) -> dict:
        return _perfil_de(self._todos(cliente_id))


# ----------------------------------------------------------------------------- escolha
class FonteMemoria:
    """Fonte a partir de uma lista de lançamentos já normalizados (testes e cenários sintéticos)."""

    nome = "memoria"

    def __init__(self, lancamentos: dict[str, list[dict]]):
        self._dados = {k: sorted(v, key=lambda l: (l["anomes"], l["dia"])) for k, v in lancamentos.items()}

    def existe(self, cliente_id: str) -> bool:
        return cliente_id in self._dados

    def extrato(self, cliente_id: str, anomes_ini: int, anomes_fim: int) -> list[dict]:
        return [l for l in self._dados.get(cliente_id, []) if int(anomes_ini) <= l["anomes"] <= int(anomes_fim)]

    def faturas(self, cliente_id: str, ate_anomes: int, n: int = 12) -> list[dict]:
        return _faturas_de(self._dados.get(cliente_id, []), int(ate_anomes), n)

    def perfil(self, cliente_id: str) -> dict:
        return _perfil_de(self._dados.get(cliente_id, []))


def fonte_padrao():
    """FonteCsv ou FonteBigQuery conforme env DADOS (padrão csv). Com bigquery, cai para CSV se o cliente não subir."""
    modo = os.environ.get("DADOS", "csv").strip().lower()
    if modo == "bigquery":
        try:
            f = FonteBigQuery()
            f._bq()
            return f
        except Exception:  # sem credencial/extra: a demo precisa funcionar com o CSV
            return FonteCsv()
    return FonteCsv()
