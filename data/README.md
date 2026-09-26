# data/

## extrato_sintetico.csv.gz

Espelho local de `batalha-time-05-xew3.hackathon_dados.extrato_sintetico` (BigQuery, us-central1), baixado por `scripts/download_bigquery.py` via `tabledata.list` (sem job SQL). Timestamps convertidos para ISO 8601 UTC. Nulos como campo vazio.

- 467.585 linhas · 1.000 `id_usuario` · 01/01/2025 a 31/12/2025 · 11 colunas · 14.727.731 bytes
- SHA-256: `d358f827c21d76ab7af524d7c0ea12dd1b62f1c7f971c25f33472042edafd1a9`
- Contagens de verificação: `tipo=E` 35.077 · `tipo=S` 432.508 · `parcela_total>1` 29.847 · `saldo_apos<0` 56.141

Use este arquivo para análise, testes e desenvolvimento. Não reexecute o download sem necessidade (custa cota e exige a conta certa).

## Colunas (tipos do BigQuery; todas NULLABLE)

| Coluna | Tipo | Interpretação | Cuidado |
|---|---|---|---|
| `id_usuario` | STRING | Identificador sintético | |
| `anomesdia` | TIMESTAMP | Data do lançamento | Todos às 00:00 UTC; sem ordem intradiária |
| `anomes` | INTEGER | Competência AAAAMM | Coerente com `anomesdia` |
| `tipo` | STRING | E entrada / S saída | Direção vem daqui; `vlr` é sempre positivo |
| `descr` | STRING | Descrição abreviada | 2 vazios; é o campo que rotula fatura integral/parcial/mínimo e os juros |
| `vlr` | FLOAT | Valor | Converter para centavos (int) |
| `nom_cate_macro` | STRING | Categoria ampla | 25 valores |
| `nom_cate_micro` | STRING | Subcategoria | 50 valores |
| `saldo_apos` | FLOAT | Saldo após o lançamento | Só fecha em 26,8% das transições; não usar como saldo corrente |
| `parcela_atual` | FLOAT | Nº da parcela | 437.738 vazios |
| `parcela_total` | FLOAT | Total de parcelas | Não identifica contrato; financiamento imobiliário vem vazio |

## Semântica descoberta (detalhe em `docs/01-evidencias-base.md`)

- Pagamento de fatura: `nom_cate_micro = "Pagamento de fatura"`; `descr` contém `integral`, `parcial` ou `minimo`.
- Rotativo: 14% ao mês sobre o não pago; mínimo = 15% da fatura; `fatura = pago + juros/0,14`.
- `Juros pagos` mistura rotativo (meses parciais/mínimos) e cheque especial (~1,3% do saldo negativo, meses de fatura inteira).
- Compras no cartão (`descr` começa com `cart credito`) e pagamento da fatura aparecem ambos como saída: não somar os dois.
- PIX recebido (`Recebimentos diversos`) pode ser renda, reembolso ou transferência própria: perguntar, não assumir.
- Unidade monetária presumida: reais.

## personas/

JSON por cliente da demo com resumo e mês a mês (valores em centavos). Gerado por `analise/persona_b.py`.
