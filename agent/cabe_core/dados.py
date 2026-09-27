"""Única porta para os dados. CSV (pandas, cache em memória) ou BigQuery (extrato bruto por cliente), mesma interface.

Escolha por env DADOS=csv|bigquery (padrão csv). Uma leitura por cliente por sessão; nunca em loop. As agregações
(fatura por modo, perfil) rodam em Python nas duas fontes, então CSV e BigQuery devolvem o mesmo número.
A camada gold do Lucas (camada_analitica/, BigQuery) é a camada-alvo: ver FonteBigQuery.

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


# ----------------------------------------------------------------------------- BigQuery
TABELA_PADRAO = "batalha-time-05-xew3.hackathon_dados.extrato_sintetico"
TABELA_90D_PADRAO = "batalha-time-05-xew3.hackathon_dados.cash90_hackathon"


def _janela(texto: str | None) -> tuple[int, int] | None:
    """'202510-202512' -> (202510, 202512); vazio ou inválido -> None (a tabela de 90 dias não é usada)."""
    if not texto:
        return None
    partes = texto.replace("–", "-").split("-")
    try:
        ini, fim = int(partes[0]), int(partes[1])
    except (IndexError, ValueError):
        return None
    return (ini, fim) if ini <= fim else None


def _dentro(pedida: tuple[int, int], cobertura: tuple[int, int]) -> bool:
    return int(cobertura[0]) <= int(pedida[0]) and int(pedida[1]) <= int(cobertura[1])


def _teto_bytes_por_consulta() -> int | None:
    """`travas.bigquery_maximum_bytes_billed` de config/finops.yaml; None (sem teto) só se o YAML não tiver a trava."""
    try:
        from . import finops   # import tardio: finops lê config/finops.yaml e não depende de dados

        teto = finops.travas().get("bigquery_maximum_bytes_billed")
        return int(teto) if teto else None
    except Exception:   # YAML ausente ou ilegível: a consulta continua parametrizada, só sem o teto
        return None


def _normalizar(r) -> dict:
    """Linha do BigQuery (mapeamento com as colunas da base) -> lançamento normalizado, igual ao do CSV."""
    descr = r["descr"] or ""
    return {
        "anomes": int(r["anomes"]), "dia": int(r["dia"]), "tipo": r["tipo"], "descr": descr,
        "valor": centavos(r["vlr"] or 0), "macro": r["nom_cate_macro"] or "", "micro": r["nom_cate_micro"] or "",
        "cartao": descr.startswith(PREFIXO_CARTAO),
        "parcela_atual": None if r["parcela_atual"] is None else int(r["parcela_atual"]),
        "parcela_total": None if r["parcela_total"] is None else int(r["parcela_total"]),
    }


class FonteBigQuery:
    """Mesma interface sobre BigQuery. Lê o extrato do cliente UMA vez por cliente por sessão e reaplica as agregações
    Python do CSV (`_faturas_de`, `_perfil_de`), para o número ser idêntico ao do CSV. Nunca consulta em loop.

    Tabelas:
    - `tabela` (env BIGQUERY_TABELA; padrão `hackathon_dados.extrato_sintetico`): o ano inteiro. É a leitura padrão,
      porque o motor precisa de até 13 faturas (grupo pelas roladas dos 12 meses anteriores) e do perfil.
    - `tabela_90d` (env BIGQUERY_TABELA_90D, ex.: `hackathon_dados.cash90_hackathon`, o "cache" D-90 do app) com
      `janela_90d` (env BIGQUERY_JANELA_90D, `AAAAMM-AAAAMM`): usada só quando o extrato pedido cabe inteiro na
      janela e ainda não há nada em cache para o cliente. Se depois o mesmo cliente precisar de histórico fora dela
      (faturas de 13 meses, perfil), a tabela completa é lida e substitui o cache (no máximo 2 consultas; sem a
      tabela de 90 dias configurada, exatamente 1).

    Camada-alvo: as tabelas gold do Lucas em `camada_analitica/` (`gold_capacidade_pagamento`, `gold_elegibilidade`,
    `gold_contexto_agente`), já no BigQuery. O motor passa a ler a gold em vez do extrato bruto quando as definições
    baterem com as do CSV (`camada_analitica/docs/alinhamento-com-o-motor.md`): fatura exata por modo em vez de
    1,33 × compras; renda recorrente por mediana de salário/INSS em vez de entradas ÷ 3; folga = renda − fixos −
    essenciais em vez de `saldo_apos`; roladas nos 12 meses anteriores ao mês de referência. Até lá a gold é
    conferida contra o motor por `analise/confere_gold_lucas.py`.

    Requer o extra `bigquery` (google-cloud-bigquery) e ADC; `executor(sql, parametros) -> iterável de linhas` permite
    injetar a consulta nos testes, sem rede. `consultas` conta as leituras feitas (vai para o trace).

    Trava de custo (padrão trazido de guiwatanabe/iai-cabe-no-bolso, `mcp_server/bq.py`): toda consulta é parametrizada
    (`@cliente_id`, nunca texto do cliente no SQL) e roda com `maximum_bytes_billed` lido de `config/finops.yaml`
    (`travas.bigquery_maximum_bytes_billed`, 1 GB); acima disso o BigQuery recusa o job em vez de cobrar.
    """

    nome = "bigquery"
    SQL_EXTRATO = (
        "SELECT id_usuario, anomes, EXTRACT(DAY FROM anomesdia) AS dia, tipo, descr, vlr, nom_cate_macro, nom_cate_micro, "
        "parcela_atual, parcela_total FROM `{tabela}` WHERE id_usuario = @cliente_id ORDER BY anomes, dia"
    )
    LOCALIZACAO = "us-central1"   # localização do dataset hackathon_dados (não confundir com a do modelo, global)

    def __init__(self, projeto: str | None = None, tabela: str | None = None, tabela_90d: str | None = None,
                 janela_90d: tuple[int, int] | None = None, executor=None, maximum_bytes_billed: int | None = None):
        self.projeto = projeto or os.environ.get("GOOGLE_CLOUD_PROJECT", "batalha-time-05-xew3")
        self.tabela = tabela or os.environ.get("BIGQUERY_TABELA", TABELA_PADRAO)
        self.tabela_90d = tabela_90d or os.environ.get("BIGQUERY_TABELA_90D") or None
        self.janela_90d = janela_90d or _janela(os.environ.get("BIGQUERY_JANELA_90D"))
        self.maximum_bytes_billed = maximum_bytes_billed if maximum_bytes_billed is not None else _teto_bytes_por_consulta()
        self._por_cliente: dict[str, dict] = {}   # cliente_id -> {"linhas": [...], "cobertura": (ini, fim) | None (= completa)}
        self._executor = executor
        self._cliente = None
        self.consultas = 0

    def _bq(self):
        if self._cliente is None:
            try:
                from google.cloud import bigquery  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("DADOS=bigquery exige o extra 'bigquery': uv sync --extra bigquery") from e
            self._cliente = bigquery.Client(project=self.projeto, location=self.LOCALIZACAO)
        return self._cliente

    def _consultar(self, tabela: str, cliente_id: str) -> list[dict]:
        sql = self.SQL_EXTRATO.format(tabela=tabela)
        self.consultas += 1
        if self._executor is not None:
            linhas = self._executor(sql, {"cliente_id": cliente_id})
        else:  # pragma: no cover  (rede; coberto pelo executor injetado nos testes)
            from google.cloud import bigquery  # type: ignore

            job = self._bq().query(sql, job_config=bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("cliente_id", "STRING", cliente_id)],
                maximum_bytes_billed=self.maximum_bytes_billed))
            linhas = job.result()
        return [_normalizar(r) for r in linhas]

    def _carregar(self, cliente_id: str, precisa: tuple[int, int] | None) -> list[dict]:
        """Lançamentos do cliente cobrindo `precisa` (None = tudo). Cache por cliente; a tabela completa vence."""
        cache = self._por_cliente.get(cliente_id)
        if cache is not None and (cache["cobertura"] is None or (precisa is not None and _dentro(precisa, cache["cobertura"]))):
            return cache["linhas"]
        if cache is None and self.tabela_90d and self.janela_90d and precisa is not None and _dentro(precisa, self.janela_90d):
            linhas = self._consultar(self.tabela_90d, cliente_id)
            self._por_cliente[cliente_id] = {"linhas": linhas, "cobertura": self.janela_90d}
            return linhas
        linhas = self._consultar(self.tabela, cliente_id)
        self._por_cliente[cliente_id] = {"linhas": linhas, "cobertura": None}
        return linhas

    def existe(self, cliente_id: str) -> bool:
        return len(self._carregar(cliente_id, None)) > 0

    def extrato(self, cliente_id: str, anomes_ini: int, anomes_fim: int) -> list[dict]:
        ini, fim = int(anomes_ini), int(anomes_fim)
        return [l for l in self._carregar(cliente_id, (ini, fim)) if ini <= l["anomes"] <= fim]

    def faturas(self, cliente_id: str, ate_anomes: int, n: int = 12) -> list[dict]:
        from .calendario import anomes_soma

        ate = int(ate_anomes)
        return _faturas_de(self._carregar(cliente_id, (anomes_soma(ate, -(int(n) - 1)), ate)), ate, n)

    def perfil(self, cliente_id: str) -> dict:
        return _perfil_de(self._carregar(cliente_id, None))


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
