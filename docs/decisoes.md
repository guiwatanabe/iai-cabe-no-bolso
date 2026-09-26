# Registro de decisões

Formato: data/hora · decisão · quem · status (proposta da análise / decidida pelo time / enviada) · evidência.

| Quando | Decisão | Quem | Status | Evidência |
|---|---|---|---|---|
| 26/09 12h35 | Persona: quem gasta olhando o limite e não o comprometido; jornada = fatura do cartão | Time | Decidida | Transcrição 12h35 |
| 26/09 12h59 | Ficha enviada: "Cabe no Bolso"; problema = financiar a fatura compromete a renda seguinte; demo = uma pessoa, uma fatura, duas saídas, acompanhar plano | Time | Enviada | `docs/arquivo/canvas-time05-submissao.md` |
| 26/09 14h10 | Mentoria: diferencial, agêntico por sinal, determinístico x LLM, consentimento, P&L, público, guardrails, métricas, impacto social | Mentora | Requisitos | `00-briefing-desafio.md` |
| 26/09 15h00 | Produto = plano de saída com data para acabar, acompanhado até 3 faturas inteiras; crédito como ferramenta | Análise | Proposta | `02-proposta-produto.md` |
| 26/09 15h40 | Tema fatura não é viés; "loop/bola de neve" e grupo único eram; quatro causas por perfil | Análise | Fato | `01-evidencias-base.md` §1–3 |
| 26/09 16h09 | Grupos A (1–2), B (3–5), C (6+) por meses rolados; B como foco; KPI = reduzir quem rola > 5x; C fora do MVP; A → cheque especial; B → consignado/prestamista | Time | Decidida | Transcrição 16h09 |
| 26/09 16h20 | Juros da base decodificados: rotativo 14% a.m.; fatura reconstruível; cheque especial ~1,3%; base não carrega saldo | Análise | Fato | `juros.py`, `juros2.py` |
| 26/09 16h45 | Correção proposta ao fechamento das 16h09: manter A/B/C como urgência; remédio pela causa; C incluído sem crédito automático; sem prestamista; bola de neve como mecânica do mercado | Análise | Proposta, pendente de aceite do time | `03-racional-prototipacao.md` §3–4 |
| 26/09 17h00 | Tese do P&L: rotativo com 65% de inadimplência é provisão, não receita; fórmula com variáveis abertas | Análise | Proposta | `02-proposta-produto.md` "A conta do banco" |
| 26/09 17h10 | Persona da demo: `3e7d20b2` (grupo B, CLT sem financiamento) | Análise | Proposta | `data/personas/3e7d20b2_grupo_b.json` |
| 26/09 17h30 | Repositório organizado; docs como fonte de verdade; build em outro ambiente | Maná | Decidida | Este commit |

## Pendências que exigem decisão do time

1. Aceitar a correção das 16h45 (remédio pela causa; C incluído; sem prestamista).
2. Confirmar a persona `3e7d20b2` para a demo.
3. Preencher donos em `08-plano-execucao.md`.
4. Conferir `config/taxas.yaml` (taxas marcadas "conferir").
5. Perguntar à staff: rubrica, tempo do pitch, se o ia.i tem plano de saída da fatura no roadmap.

Regra: reabrir decisão só com fato novo registrado aqui.
