# Cabe no Bolso · Time 05 · Batalha de Agentes (Itaú × Google Cloud), 26–27/09/2026

Agente que ajuda o cliente a sair da fatura do cartão rolada com um plano que cabe no mês dele, e fica com ele até três faturas inteiras seguidas. Cliente paga menos e sabe quando termina; o banco troca juro de rotativo que não recebe (65% de inadimplência, BC jul/2026) por parcela paga e cliente que fica.

## Comece por aqui

1. `CLAUDE.md`: regras de construção, stack, ambiente, o que não fazer.
2. `docs/02-proposta-produto.md`: o produto, valor para cliente e banco, métricas, pitch.
3. `docs/04-arquitetura-gcp.md` e `docs/05-responsible-ai.md`: como construir e o que o código impõe.
4. `docs/07-demo-roteiro.md` e `docs/08-plano-execucao.md`: o que entregar amanhã e em que ordem.
5. `docs/decisoes.md`: o que está decidido, o que é proposta, o que está pendente.

## Estrutura

```
CLAUDE.md  README.md
docs/          00-briefing · 01-evidencias · 02-proposta · 03-racional · 04-arquitetura · 05-responsible-ai
               06-design-system · 07-demo · 08-plano · decisoes.md · arquivo/
data/          extrato_sintetico.csv.gz (14,7 MB) · personas/ · README.md (dicionário)
analise/       scripts que geraram os números (README.md)
config/        taxas.yaml (única fonte de taxas e parâmetros)
agent/         especificação do agente ADK + núcleo determinístico
demo/          especificação da demo web (QR code)
fontes/        case oficial, template da ficha, guia GCP, transcrições
output/        fichas e PPTX enviados
scripts/       download_bigquery.py
```

## Ambiente

Python 3.11 + `uv`; Google ADK + Gemini; BigQuery `batalha-time-05-xew3.hackathon_dados.extrato_sintetico` (us-central1); Cloud Run. Em dev, tudo roda sobre o CSV local. Autenticação e custos em `CLAUDE.md`.

## Status (26/09, 17h30)

Ficha enviada. Análise da base concluída e documentada. Proposta de produto e arquitetura escritas. Código do agente e da demo: a construir (ver `docs/08`). Pendências de decisão do time em `docs/decisoes.md`.
