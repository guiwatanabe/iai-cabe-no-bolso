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
- `ROLANDO_FATURA` + falta `RECORRENTE`: avaliar parcelamento/crédito.
- `NO_LIMITE`: fora do MVP.
- Crédito pré-aprovado/decisão de crédito não é responsabilidade da LLM.

## Projeção de caixa por ciclo (`silver_ciclo_fatura`)

Substitui o snapshot de `saldo_apos` (não confiável: fecha em apenas 26,8% das
transições) por uma projeção de caixa por ciclo de fatura, calculada só a
partir de entradas e saídas (nunca do saldo informado na origem).

**Definição de ciclo.** Em vez de reconstruir o calendário de vencimentos a
partir de `dia_vencimento` (o que exigiria tratar meses de tamanhos
diferentes e clientes sem um dia fixo), usamos as datas reais dos
pagamentos de fatura (`nom_cate_micro = 'Pagamento de fatura'`) na janela
D-90 como proxy do vencimento de cada ciclo. Na prática, todo cliente da
janela tem exatamente 3 pagamentos de fatura em 90 dias (checado na massa
sintética completa via emulação local), então "3 ciclos" é observado, não
apenas assumido. O ciclo 3 é o mais recente (o ciclo "corrente", cuja fatura
tratamos como ainda não fechada para fins da simulação); os ciclos 1 e 2 são
os dois anteriores, já totalmente observados.

Para cada ciclo `c`, com início = vencimento do ciclo anterior (ou o início
da janela D-90, para o primeiro ciclo) e fim = data do pagamento que define
o ciclo:

- `entradas_ate_venc(c)` = soma de `vlr` das entradas (`tipo='E'`) no
  intervalo `(início, fim]`.
- `saidas_nao_cartao_ate_venc(c)` = soma de `vlr` das saídas no mesmo
  intervalo, **excluindo** pagamento de fatura e compras no cartão (mesmo
  padrão de `silver_cartao`/`silver_transacoes_resumo`: `descr LIKE
  '%cart credito%' AND descr NOT LIKE '%pag%fat%'`).
- `saldo_previsto_vencimento(c) = entradas_ate_venc(c) - saidas_nao_cartao_ate_venc(c)`.
- `fatura_estimada(c)` = a mesma fatura 1,33x de `silver_cartao`, casada pelo
  `anomes` do fim do ciclo `c` (compras do mês anterior ao vencimento).
- `falta(c) = GREATEST(fatura_estimada(c) - saldo_previsto_vencimento(c), 0)`.

**`data_simulada` e a cauda do ciclo corrente (R19).** `data_simulada` =
vencimento do ciclo 3 menos 5 dias — o "hoje" simulado da demo. Fluxos reais
entre `data_simulada` e o vencimento do ciclo 3 já aconteceram na base
histórica, mas do ponto de vista da simulação ainda não são conhecidos, por
isso são descartados e substituídos pela média dos mesmos 5 dias finais
(a "cauda") dos ciclos 1 e 2:

```
entradas_ate_venc(3) = entradas reais em (início_3, data_simulada]
                      + média(entradas nos últimos 5 dias dos ciclos 1 e 2)
saidas_nao_cartao_ate_venc(3) = saidas reais em (início_3, data_simulada]
                      + média(saidas_nao_cartao nos últimos 5 dias dos ciclos 1 e 2)
```

Essa é uma simplificação deliberada da ideia geral de "usar valores
típicos": em vez de modelar cada rubrica separadamente, tratamos a cauda de
5 dias como uma unidade e usamos a média histórica dela nos dois ciclos
anteriores.

**`tipo_falta`** (gold): `SEM_FALTA` se `falta(3) = 0`; `RECORRENTE` se
`falta(c) > 0` em pelo menos 2 dos 3 ciclos; caso contrário `PONTUAL`.

**Limitação conhecida / pergunta em aberto.** Rodando essa lógica sobre uma
emulação local (últimos 90 dias do CSV `extrato_sintetico.csv.gz`, fora do
BigQuery), a segmentação de 12 meses bateu exatamente 311/101/384/204, e o
`% SEM_FALTA` cai de forma monotônica com a severidade do grupo
(SEMPRE_QUITA 96,1% > ESCORREGAO 88,1% > ROLANDO_FATURA 80,2% > NO_LIMITE
73,5%), assim como o `% RECORRENTE` sobe (1,9% < 8,9% < 16,4% < 18,6%). Mas
a leitura literal da expectativa da seção 5 do plano — "ESCORREGAO é o mais
PONTUAL, ROLANDO_FATURA é o mais RECORRENTE" como máximos absolutos entre
grupos — não se confirma: `NO_LIMITE` tem a maior fatia de `PONTUAL` (7,8%)
e de `RECORRENTE` (18,6%) de todos os grupos, ligeiramente acima de
`ROLANDO_FATURA` (RECORRENTE 16,4%). Isso é esperado, já que `NO_LIMITE` é o
grupo mais severo pela própria definição de pedaladas em 12 meses; a
janela D-90 de 3 ciclos por cliente é uma amostra mais curta que os 12
meses da segmentação, então nem todo cliente `ROLANDO_FATURA` (3-5
pedaladas em 12 meses) necessariamente "pedala" dentro dos 3 ciclos
observados. Reportando em vez de ajustar a fórmula silenciosamente,
conforme pedido no plano; ver `sql/checks/04_tipo_falta_por_grupo.sql` para
rodar a checagem real contra `gold_contexto_agente` no BigQuery.

## Observação

As regras de `PONTUAL`/`RECORRENTE` acima já usam o saldo projetado no
vencimento (não mais o saldo atual/snapshot).
