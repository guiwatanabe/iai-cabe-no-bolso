# Homologação

## Segmentação

Após correção das regras semânticas:

| Grupo | Clientes |
|---|---:|
| SEMPRE_QUITA | 311 |
| ESCORREGAO | 101 |
| ROLANDO_FATURA | 384 |
| NO_LIMITE | 204 |
| **Total** | **1.000** |

A distribuição esperada foi recuperada exatamente.

## Fatura estimada

Validação do fator `1.33x` contra 7.823 pagamentos integrais:

| Métrica | Resultado |
|---|---:|
| Fatura real média | R$ 1.754,70 |
| Fatura estimada média | R$ 1.795,10 |
| Erro mediano | -0,58% |
| Erro médio | +11,76% |
| Fator real mediano | 1,338 |

A diferença em relação aos 8.082 pagamentos mencionados na especificação ocorre porque 259 observações não possuem `compras_mes_anterior` disponível no `LAG`.

A `silver_cartao` ficou praticamente limpa, com apenas um registro identificado como contaminado (R$ 13,18, tarifa de pedágio).

## Bugs semânticos corrigidos

### Histórico de fatura

A regra original baseada apenas em `%fatura%` perdia descrições abreviadas com `fat`.

Correção:

```sql
(descricao LIKE '%fatura%' OR descricao LIKE '%fat%cart%')
```

### Compras de cartão

As compras usam `cart credito`, não `cartao`.

Correção:

```sql
LIKE '%cart credito%'
AND NOT LIKE '%pag%fat%'
```

## Limitações conhecidas

### Folga mensal

`folga_mensal_estimada` ainda é:

```text
renda_mensal_estimada - gasto_recorrente_mensal_estimado
```

Não incorpora integralmente parcelas futuras e fatura estimada.

### Saldo previsto no vencimento

O motor atual utiliza o último `saldo_apos` como `saldo_atual`.

Isso é um snapshot e não representa necessariamente o caixa disponível no vencimento da fatura.

A homologação mostrou:

| Grupo | Fatura cabe | % |
|---|---:|---:|
| SEMPRE_QUITA | 304/311 | 98% |
| ESCORREGAO | 72/101 | 71% |
| ROLANDO_FATURA | 273/384 | 71% |
| NO_LIMITE | 180/204 | 88% |

O resultado de `NO_LIMITE` evidencia a limitação do snapshot.

### Recomendações Gold

Distribuição observada na versão atual:

| Recomendação | Clientes |
|---|---:|
| SEM_INTERVENCAO | 829 |
| CONSULTAR_CREDITO | 83 |
| SEM_OFERTA_AUTOMATICA | 80 |
| COBERTURA_CURTA | 8 |

Esses números reproduzem o motor atual, mas **não devem ser interpretados como resultado final de negócio**, pois dependem das limitações de folga e saldo descritas acima.
