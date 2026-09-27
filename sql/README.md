# Cabe no Bolso --- Camada Analítica

Camada analítica do protótipo **Cabe no Bolso**, responsável por
transformar os dados do hackathon em contexto financeiro estruturado
para consumo pelas Tools do agente.

## Arquitetura

``` text
Bronze → Silver → Gold → Tools → Agente Cabe no Bolso / ia.i
```

### BigQuery

**Projeto:** `batalha-time-05-xew3`\
**Dataset:** `hackathon_dados`

Fontes:

-   `extrato_sintetico` --- histórico completo de 2025.
-   `cash90_hackathon` --- janela D-90 utilizada pelo motor analítico.

## Silver

Tabelas responsáveis pela preparação dos dados e criação das features:

-   `silver_cliente_dia`
-   `silver_recebimentos`
-   `silver_historico_fatura_12m`
-   `silver_compromissos`
-   `silver_cartao`
-   `silver_cliente_features`
-   `silver_ciclo_fatura`
-   `silver_transacoes_resumo`

## Gold

Tabelas consumidas pelo motor e pelas Tools:

-   `gold_capacidade_pagamento`
-   `gold_elegibilidade`
-   `gold_contexto_agente`

A `gold_contexto_agente` representa o contrato final com o agente, com
uma linha por cliente e valores monetários em centavos (`INT64`).

## Princípio

Os cálculos financeiros são realizados na camada analítica.

**SQL/Python calculam. A LLM conversa.**

A LLM recebe o contexto estruturado pelas Tools e não deve recalcular
capacidade financeira ou decidir crédito.

## Execução

Execute os SQLs nesta ordem:

``` text
sql/silver/  → 01 a 08
sql/gold/    → 01 a 03
```

Depois, utilize:

``` text
sql/checks/
```

para as consultas de homologação.

## Status

-   Silver 01--08: ✅ Homologada
-   Gold 01--03: ✅ Homologada
-   1.000 clientes processados

## Documentação

Mais detalhes estão disponíveis em:

-   `docs/arquitetura.md`
-   `docs/regras-negocio.md`
-   `docs/homologacao.md`
-   `docs/mudancas.md`

## Próximo passo

Integrar a camada Gold às Tools e ao agente **Cabe no Bolso**.
