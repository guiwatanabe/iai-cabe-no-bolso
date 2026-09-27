# Regras de Negócio

## Segmentação histórica

A quantidade de meses com pagamento parcial/mínimo nos 12 meses determina o grupo:

| Pedaladas | Grupo |
|---:|---|
| 0 | `SEMPRE_QUITA` |
| 1–2 | `ESCORREGAO` |
| 3–5 | `ROLANDO_FATURA` |
| 6+ | `NO_LIMITE` |

## Identificação de pagamento parcial/mínimo

Foram encontrados padrões diferentes no extrato:

- `pag fatura cartao parcial/minimo`
- `pag fat cartao parcial/minimo`
- `pag fat cart credito parcial/minimo`

Por isso a detecção considera:

```sql
(descricao LIKE '%fatura%' OR descricao LIKE '%fat%cart%')
```

com `parcial`, `minimo` ou `mínimo`.

## Compras no cartão

As compras reais usam o padrão:

```text
cart credito ...
```

A regra utilizada é:

```sql
LIKE '%cart credito%'
```

Pagamentos de fatura são excluídos por:

```sql
NOT LIKE '%pag%fat%'
```

## Estimativa da fatura

No protótipo:

```text
fatura_estimada = compras_cartao_mes_anterior × 1.33
```

O fator foi homologado contra pagamentos integrais.

## Elegibilidade MVP

- `ESCORREGAO` + falta `PONTUAL`: avaliar cobertura curta.
- `ROLANDO_FATURA` + falta `ESTRUTURAL`: avaliar parcelamento/crédito.
- `NO_LIMITE`: fora do MVP.
- Crédito pré-aprovado/decisão de crédito não é responsabilidade da LLM.

## Observação

As regras atuais de `PONTUAL` e `ESTRUTURAL` são provisórias porque o MVP ainda utiliza saldo atual em vez de saldo projetado no vencimento.
