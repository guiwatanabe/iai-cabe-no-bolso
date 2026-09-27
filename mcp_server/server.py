"""MCP server: curated, read-only tools over the client's gold row. No LLM logic lives here.

Every tool returns a `Result` envelope. BigQuery and `mcp_server/core` compute every number
the answer needs and expose it as a `Fact`; the agent never does arithmetic.
Money facts are in reais (BRL), rates in percentage points (pct).
"""

import unicodedata
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from mcp_server import dados
from mcp_server.core import acompanhamento, fatura, ofertas, rotativo, taxas
from mcp_server.core.tipos import EstadoSessao

mcp = MCPServer("cabe-no-bolso", instructions="Read-only tools over the client's current credit card bill.")
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

Unit = Literal["BRL", "pct", "dia", "dias", "parcelas"]


class Fact(BaseModel):
    label: str = Field(description="What the value means, e.g. 'valor da fatura atual'")
    value: int | float | str
    unit: Unit | None = Field(
        None,
        description="BRL = reais; pct = percentage points (3.5 means 3.5%); dia = day of month; "
        "dias = number of days; parcelas = number of installments",
    )


class Result(BaseModel):
    status: Literal["ok", "empty"]
    facts: dict[str, Fact] = {}
    rows: list[dict[str, Any]] = []
    contexto: dict[str, Any] = {}
    meta: dict[str, Any] = {}


GOLD = "gold_contexto_agente"
SIMULACAO = "data, taxas, liberação de crédito e meses seguintes são simulados"
TIPO_DE_FALTA = {"SEM_FALTA": "nenhuma", "PONTUAL": "pontual", "RECORRENTE": "recorrente"}
PRODUTO = {
    "cobertura_cheque_especial": "cobertura com cheque especial",
    "credito_consignado": "crédito consignado",
    "credito_prestamista": "crédito com seguro prestamista",
}
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
TOP_CATEGORIAS = 5

# source field -> (fact key, label, unit). Labels must stand alone: the model maps facts by label.
Campos = dict[str, tuple[str, str, Unit]]
FATURA: Campos = {
    "valor_c": ("fatura_valor", "valor da fatura atual", "BRL"),
    "dia_vencimento": ("fatura_vencimento", "dia de vencimento da fatura atual", "dia"),
    "minimo_c": ("fatura_minimo", "pagamento mínimo da fatura atual", "BRL"),
    "encargos_se_pagar_minimo_c": (
        "fatura_encargos_se_pagar_minimo",
        "encargos de um mês de rotativo se pagar só o mínimo da fatura atual",
        "BRL",
    ),
}
PARCELAMENTO: Campos = {
    "parcela_c": ("parcela", "valor de cada parcela ao parcelar a fatura atual em", "BRL"),
    "custo_total_c": ("custo_total", "custo total ao parcelar a fatura atual em", "BRL"),
}
CAPACIDADE: Campos = {
    "saldo_previsto_vencimento_c": (
        "saldo_previsto_vencimento",
        "saldo previsto na conta no dia do vencimento da fatura",
        "BRL",
    ),
    "valor_recebimento_tipico_c": ("proximo_recebimento_valor", "valor do próximo recebimento (salário)", "BRL"),
    "dia_recebimento_estimado": ("proximo_recebimento_dia", "dia do mês do próximo recebimento", "dia"),
    "dias_ate_recebimento": (
        "dias_ate_recebimento",
        "dias entre o vencimento da fatura e o próximo recebimento",
        "dias",
    ),
    "folga_mensal_c": ("folga_mensal", "folga mensal (renda menos gastos recorrentes)", "BRL"),
    "valor_faltante_c": ("falta_prevista", "quanto falta na conta para pagar a fatura atual inteira", "BRL"),
    "parcelas_em_curso_c": ("parcelas_em_curso", "valor mensal das parcelas de compras já em andamento", "BRL"),
}
OFERTA: Campos = {
    "custo_total_c": ("custo_total", "custo total", "BRL"),
    "taxa_mes": ("taxa", "taxa de juros ao mês", "pct"),
    "prazo_dias": ("prazo", "prazo em dias", "dias"),
    "parcela_c": ("parcela", "valor de cada parcela", "BRL"),
    "parcelas": ("parcelas", "número de parcelas", "parcelas"),
    "novo_limite_c": ("novo_limite", "novo limite do cheque especial", "BRL"),
}
ROTATIVO: Campos = {
    "custo_c": (
        "rotativo_custo_1_mes",
        "custo de deixar no rotativo por um mês o valor que falta para pagar a fatura",
        "BRL",
    )
}
ACOMPANHAMENTO: Campos = {
    "valor_c": ("acompanhamento_valor_coberto", "valor coberto pela cobertura aceita", "BRL"),
    "dia_recebimento": ("acompanhamento_dia_recebimento", "dia do recebimento que quita a cobertura aceita", "dia"),
    "parcelas_pagas": ("acompanhamento_parcelas_pagas", "parcelas já pagas do crédito aceito", "parcelas"),
    "parcelas": ("acompanhamento_parcelas", "número total de parcelas do crédito aceito", "parcelas"),
    "proxima_parcela_c": ("acompanhamento_proxima_parcela", "valor da próxima parcela do crédito aceito", "BRL"),
    "dia_proxima": ("acompanhamento_dia_proxima_parcela", "dia da próxima parcela do crédito aceito", "dia"),
    "limite_liberado_c": ("acompanhamento_limite_liberado", "limite do cartão liberado pelo crédito aceito", "BRL"),
}


class _Facts:
    """Collects facts (centavos -> reais, fraction -> percentage points) and the origin of each."""

    def __init__(self):
        self.facts: dict[str, Fact] = {}
        self.origem: dict[str, Any] = {}

    def one(self, key: str, label: str, value: float, unit: Unit, origem: Any) -> None:
        if unit == "BRL":
            value = value / 100
        elif unit == "pct":
            value = round(value * 100, 4)
        self.facts[key] = Fact(label=label, value=value, unit=unit)
        self.origem[key] = origem

    def add(self, campos: Campos, values: dict, origem: Any, prefix: str = "", suffix: str = "") -> None:
        for campo, (key, label, unit) in campos.items():
            if values.get(campo) is not None:
                o = origem.get(campo) if isinstance(origem, dict) else origem
                self.one(prefix + key, label + suffix, values[campo], unit, o)


@mcp.tool(annotations=READ_ONLY)
def contexto_fatura(cliente_id: str = "", estado: EstadoSessao | None = None) -> Result:
    """Everything about the client's current credit card bill: value, due day, minimum, ways to pay,
    and, with consent, the cash forecast, released offers and their costs. Call it before answering.

    Takes no arguments: `cliente_id` and `estado` are filled in by the session, never by the model.
    """
    ctx = dados.contexto(cliente_id)
    meta: dict[str, Any] = {"source": [dados.GOLD, "taxas.yaml"]}
    if ctx is None:
        return Result(status="empty", meta=meta)
    estado = estado or EstadoSessao()
    tx = taxas.carregar()
    f = _Facts()

    formas = fatura.formas_de_pagar(ctx, tx)
    origem = formas.get("origem", {})
    f.add(FATURA, formas, origem)
    for p in formas["parcelamento_fatura"]:
        n = p["parcelas"]
        f.add(PARCELAMENTO, p, origem.get("parcelamento_fatura"), f"parcelamento_fatura_{n}x_", f" {n}x")
    contexto: dict[str, Any] = {
        "consentimento": estado.consentimento,
        "primeiro_nome": estado.primeiro_nome,
        "formas_de_pagar": ["total", *(["parcelamento da fatura"] if formas["parcelamento_fatura"] else []), "mínimo"],
    }
    if not estado.consentimento:
        return Result(status="ok", facts=f.facts, contexto=contexto, meta=meta | {"origem": f.origem})

    f.add(CAPACIDADE, ctx.model_dump(), GOLD)
    liberadas = ofertas.ofertas_liberadas(ctx, estado, tx)
    for o in liberadas:
        nome = f" da oferta {o['id']} ({PRODUTO.get(o['tipo'], o['tipo'])})"
        f.add(OFERTA, o, o.get("origem"), f"oferta_{o['id']}_", nome)
    rot = rotativo.custo_rotativo(ctx.valor_faltante_c, 1, tx)
    f.add(ROTATIVO, rot, rot.get("origem", "core.rotativo"))
    mudar = ofertas.mudar_vencimento(ctx)
    origem_dias = mudar.get("origem", {}).get("dias", "core.ofertas")
    for i, dia in enumerate(mudar.get("dias", []) if mudar["disponivel"] else [], 1):
        f.one(f"mudar_vencimento_dia_{i}", f"novo dia de vencimento sugerido (opção {i})", dia, "dia", origem_dias)
    acomp = acompanhamento.acompanhamento(ctx, estado)
    if acomp:
        f.add(ACOMPANHAMENTO, acomp, acomp.get("origem", "core.acompanhamento"))
        acomp = {k: v for k, v in acomp.items() if k not in ACOMPANHAMENTO and k != "origem"}

    contexto |= {
        "publico_vulneravel": ctx.publico_vulneravel,
        "tipo_de_falta": TIPO_DE_FALTA[ctx.tipo_falta],
        "ofertas_liberadas": [{k: o[k] for k in ("id", "tipo", "amplia_limite")} for o in liberadas],
        "mudar_vencimento": {"disponivel": mudar["disponivel"]},
        "acompanhamento": acomp or None,
        "simulacao": SIMULACAO,
    }
    return Result(status="ok", facts=f.facts, contexto=contexto, meta=meta | {"origem": f.origem})


@mcp.tool(annotations=READ_ONLY)
def explicar_fatura(cliente_id: str = "") -> Result:
    """Why the bill came high: card purchases of the bill month by category, largest first.

    Takes no arguments: `cliente_id` is filled in by the session, never by the model.
    """
    rows = dados.transacoes(cliente_id)
    meta = {"source": [dados.TRANSACOES], "row_count": len(rows)}
    if not rows:
        return Result(status="empty", meta=meta)
    facts = {}
    for r in rows[:TOP_CATEGORIAS]:
        mes, cat = f"{MESES[r['anomes'] % 100 - 1]}/{r['anomes'] // 100}", r["categoria"]
        label = f"parcelas de compras anteriores em {mes}" if cat == "Parcelas" else f"compras em {cat} em {mes}"
        facts[f"compras_{_slug(cat)}"] = Fact(label=label, value=r["valor_c"] / 100, unit="BRL")
    return Result(status="ok", facts=facts, rows=rows, meta=meta)


def _slug(text: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "_".join("".join(c if c.isalnum() else " " for c in ascii_).split())


if __name__ == "__main__":
    mcp.run()
