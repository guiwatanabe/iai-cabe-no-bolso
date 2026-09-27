"""Shared contracts between the SQL layer, the pure core and the MCP tools.

Money is always int centavos (`_c` suffix). Formatting to text happens only at the
edge (grounding in the agent), never here.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

Grupo = Literal["SEMPRE_QUITA", "ESCORREGAO", "ROLANDO_FATURA", "NO_LIMITE"]


class Contexto(BaseModel):
    """One row of `gold_contexto_agente` (sql/gold/03_contexto_agente.sql)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id_usuario: str
    data_simulada: date  # demo "today": a few days before the last bill's due day
    grupo_cliente: Grupo
    qtd_pedaladas_12m: int  # decision only, never shown to the model
    dia_vencimento: int
    fatura_estimada_c: int  # 1.33 x card purchases of the previous month
    saldo_previsto_vencimento_c: int  # cycle cash projection, not saldo_apos
    renda_mensal_estimada_c: int
    valor_recebimento_tipico_c: int
    dia_recebimento_estimado: int
    dias_ate_recebimento: int  # from the due day to the next expected receipt
    confianca_recebimento: Literal["ALTA", "MEDIA", "BAIXA"]
    folga_mensal_c: int  # renda - recorrentes (medallion definition, known to be overstated)
    valor_faltante_c: int  # max(fatura - saldo previsto, 0)
    tipo_falta: Literal["SEM_FALTA", "PONTUAL", "RECORRENTE"]
    parcelas_em_curso_c: int  # monthly amount of installments already running
    publico_vulneravel: bool  # INSS beneficiary in the window
    elegivel_cobertura_curta: bool
    elegivel_parcelamento: bool


class Liberacao(BaseModel):
    """Simulated answer of the bank's credit service (demo parameter)."""

    cobertura: bool = True
    consignado: bool = True
    prestamista: bool = True


class EstadoSessao(BaseModel):
    """Session state the agent's before_tool callback injects into tool calls (never filled by the model)."""

    consentimento: bool = False
    primeiro_nome: str = ""
    liberacao: Liberacao = Liberacao()
    credito_usado_12m: bool = False  # a Cabe no Bolso credit was taken in the last 12 months
    reincidencia_pos_credito: bool = False  # bill paid below total within 3 months of that credit
    cheque_especial_zerado: bool = True  # account back to positive since the last coverage
    oferta_aceita: dict | None = None  # {"id", "tipo", ...} once the client accepts; feeds acompanhamento
