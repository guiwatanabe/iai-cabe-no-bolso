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

### Gold

- `gold_capacidade_pagamento`
- `gold_elegibilidade`
- `gold_contexto_agente`

## Princípio de arquitetura

Os cálculos financeiros são realizados deterministicamente na camada analítica. A LLM não deve calcular capacidade financeira; ela recebe contexto estruturado pelas Tools e conduz a conversa.

## Execução

Execute os SQLs pela ordem numérica dentro de `sql/silver/` e depois `sql/gold/`.

Consulte `docs/homologacao.md` antes de interpretar as saídas Gold, pois existem limitações conhecidas no MVP.
