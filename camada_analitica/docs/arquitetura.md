# Arquitetura de Dados

## Visão geral

```text
extrato_sintetico ──────────────┐
                                │
cash90_hackathon ───────────────┤
                                ▼
                         ┌─────────────┐
                         │   SILVER    │
                         │ cliente_dia │
                         │ recebimentos│
                         │ compromissos│
                         │ hist_fatura │
                         │ cartao      │
                         │ features    │
                         └──────┬──────┘
                                ▼
                         ┌─────────────┐
                         │    GOLD     │
                         │ capacidade  │
                         │ elegibilid. │
                         │ contexto    │
                         └──────┬──────┘
                                ▼
                              TOOLS
                                ▼
                       CABE NO BOLSO
                                ▼
                               ia.i
```

## Bronze

As fontes permanecem brutas:

- `extrato_sintetico`: histórico anual.
- `cash90_hackathon`: janela de 90 dias.

## Silver

Responsável por limpeza, agregações e features determinísticas.

A segmentação de 12 meses é calculada fora do contexto D-90 para preservar o histórico necessário à classificação das personas.

## Gold

Transforma as features Silver em saídas operacionais para as Tools:

1. capacidade de pagamento;
2. elegibilidade;
3. contexto consolidado do agente.

## Tools

A próxima camada deverá consultar as tabelas Gold e devolver JSON estruturado ao agente. A LLM é responsável pela interação conversacional, e não pelos cálculos financeiros.
