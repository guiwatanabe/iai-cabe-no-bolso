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

### Saldo previsto no vencimento (corrigido, R3)

O motor passou a usar a projeção de caixa por ciclo (`silver_ciclo_fatura`,
detalhada em `regras-negocio.md`) em vez do último `saldo_apos`, que é um
snapshot não confiável (fecha em apenas 26,8% das transições) e não
representa o caixa disponível no vencimento.

A tabela abaixo (`fatura cabe` = 88%/71%/71%/98% por grupo) refletia a
limitação do snapshot antigo e não se aplica mais; **não foi recalculada no
BigQuery neste ciclo** (regra local-only, ver `sql/checks/*.sql` para as
consultas a rodar após a carga). Uma emulação local com os últimos 90 dias
do CSV `extrato_sintetico.csv.gz` (fora do BigQuery, ver `regras-negocio.md`
§ "Projeção de caixa por ciclo") mostrou o `% SEM_FALTA` caindo
monotonicamente com a severidade do grupo (96,1% → 88,1% → 80,2% → 73,5% de
SEMPRE_QUITA a NO_LIMITE), mas não confirmou a expectativa literal de que
ESCORREGAO seria o grupo mais `PONTUAL` e ROLANDO_FATURA o mais
`RECORRENTE` em termos absolutos — `NO_LIMITE`, sendo o grupo mais severo,
teve as maiores fatias de ambos. Ver a observação em `regras-negocio.md`.

### Recomendações Gold (números desatualizados)

A tabela abaixo era da versão com `saldo_atual` (snapshot) e `tipo_falta`
`ESTRUTURAL`; **não foi recalculada** com a projeção de caixa por ciclo nem
com o novo `RECORRENTE`, e `recomendacao_motor` não faz mais parte de
`gold_contexto_agente` (fora das colunas de `Contexto`; continua disponível
como `acao_motor` em `gold_elegibilidade` para depuração):

| Recomendação (antiga) | Clientes |
|---|---:|
| SEM_INTERVENCAO | 829 |
| CONSULTAR_CREDITO | 83 |
| SEM_OFERTA_AUTOMATICA | 80 |
| COBERTURA_CURTA | 8 |

Esses números **não devem ser interpretados como resultado atual**; rodar
`sql/checks/04_tipo_falta_por_grupo.sql` contra o `gold_contexto_agente`
recarregado para obter a distribuição real com a projeção de caixa por
ciclo.
