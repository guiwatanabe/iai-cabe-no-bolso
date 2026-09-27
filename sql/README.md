# Cabe no Bolso — Camada Analítica

Camada analítica do protótipo **Cabe no Bolso**, responsável por transformar os dados do extrato sintético do hackathon em contexto financeiro estruturado para consumo pelas Tools do agente.

## Arquitetura

`Bronze → Silver → Gold → Tools → Agente Cabe no Bolso / ia.i`

### Fontes no BigQuery

Projeto: `batalha-time-05-xew3`  
Dataset: `hackathon_dados`

- `hackathon_dados.extrato_sintetico` — histórico completo de 2025.
- `hackathon_dados.cash90_hackathon` — janela D-90 utilizada pelo motor analítico.

### Silver

- `silver_cliente_dia`
- `silver_recebimentos`
- `silver_historico_fatura_12m`
- `silver_compromissos`
- `silver_cartao`
- `silver_cliente_features`
- `silver_ciclo_fatura` — projeção de caixa por ciclo de fatura (substitui o snapshot de `saldo_apos`; ver "Projeção de caixa por ciclo" em `docs/regras-negocio.md`)
- `silver_transacoes_resumo` — compras de cartão por categoria e mês (D-90), consumida por `explicar_fatura`

### Gold

- `gold_capacidade_pagamento`
- `gold_elegibilidade`
- `gold_contexto_agente` — contrato exato de `mcp_server/core/tipos.py::Contexto`; dinheiro em centavos INT64; uma linha por cliente

## Princípio de arquitetura

Os cálculos financeiros são realizados deterministicamente na camada analítica. A LLM não deve calcular capacidade financeira; ela recebe contexto estruturado pelas Tools e conduz a conversa.

## Execução

Execute os SQLs pela ordem numérica dentro de `sql/silver/` (01 a 08) e depois `sql/gold/` (01 a 03). `silver_ciclo_fatura` (07) depende de `silver_cartao` (05); `gold_capacidade_pagamento` depende de `silver_cliente_features` (06) e `silver_ciclo_fatura` (07).

Os scripts em `sql/checks/*.sql` não fazem parte do pipeline de criação; são consultas de homologação para rodar manualmente contra `gold_contexto_agente` depois da carga (não foram executadas neste ciclo — ver `docs/homologacao.md`).

O que mudou em relação à camada original, e por quê: `docs/mudancas.md`.

Consulte `docs/homologacao.md` antes de interpretar as saídas Gold, pois existem limitações conhecidas no MVP.
