# Mudanças em relação à camada analítica original

Base de comparação: `camada_analitica/` do repositório de análise (`cabe-no-bolso-hml`, commit "Inclusão da camada analítica"), copiada sem alterações em `sql/` no commit `b7ff0b0`. As mudanças estão nos commits `5bd472b` e `aec39c4`. Nada disso foi executado no BigQuery ainda: o SQL só foi validado por sintaxe (sqlglot, dialeto BigQuery) e por uma emulação local sobre o CSV (os últimos 90 dias de 2025 fazendo o papel de `cash90_hackathon`).

## Por que o modelo mudou

A camada original foi escrita antes da Spec do produto (27/09) e antes de existir um consumidor concreto. Três coisas fizeram o modelo mudar:

1. **A Spec definiu o que o motor precisa dizer.** A frase de abertura da conversa é "faltam R$ X no dia Y, e o dinheiro volta em Z dias" (falta pontual) ou "faltam R$ X todo mês" (falta recorrente). Para isso o gold precisa de saldo previsto no vencimento, dia do vencimento, próximo recebimento, dias até o recebimento, se a falta se repete, parcelas em curso e se o cliente é aposentado. A maior parte não existia.
2. **Um campo da base não é confiável.** `saldo_apos` fecha em apenas 26,8% das transições (`data/README.md` do repositório de análise). O motor original usava o último `saldo_apos` como saldo atual, e dele saíam a falta, o "fatura cabe", o tipo de falta e a recomendação. A própria homologação original mostrou o sintoma: NO_LIMITE aparecia com "fatura cabe" em 88% dos casos, mais que ESCORREGAO (71%).
3. **Um consumidor com contrato.** As ferramentas do agente (`mcp_server/`) leem uma linha por cliente de `gold_contexto_agente` e validam contra `mcp_server/core/tipos.py::Contexto`. Dinheiro passou a ir em centavos inteiros, que é a convenção do código.

Regras de negócio e elegibilidade de crédito (prazo da cobertura, parcela contra folga, uma vez a cada 12 meses, liberação do serviço de crédito) ficaram no Python (`mcp_server/core/ofertas.py`), porque dependem de taxas (`taxas.yaml`) e do estado da sessão. O SQL entrega os fatos do cliente e as flags de elegibilidade do motor.

## Mudanças por tabela

| Tabela | O que mudou | Por quê |
|---|---|---|
| `silver_recebimentos` | `dia_recebimento_estimado` passou a ser a moda do dia de `Salario CLT` / `Beneficio INSS`, com a moda de todas as entradas como reserva. Nova coluna `publico_vulneravel` (houve `Beneficio INSS` na janela). | A mediana de todas as entradas misturava PIX e dava um dia de recebimento ruidoso, e o prazo da cobertura curta depende desse dia (R11). A Spec trata aposentados como público vulnerável: sem consignado sem confirmação humana (R17). |
| `silver_compromissos` | `parcelas_futuras_estimadas` usa só as linhas do último mês da janela. Nova coluna `parcelas_em_curso` (valor mensal das parcelas em andamento). | A mesma compra parcelada aparece todo mês (3/12, 4/12, 5/12) e cada aparição somava de novo o saldo restante. Na persona `3e7d20b2` isso contava 24 parcelas restantes onde havia 7 (R5). A Spec mostra "parcelas em curso" ao cliente. |
| `silver_cliente_features` | Passa adiante `confianca_recebimento`, `publico_vulneravel` e `parcelas_em_curso`. | `confianca_recebimento` era calculada e descartada. A Spec diz que, com renda irregular, não há oferta (R15). |
| `silver_ciclo_fatura` (nova) | Projeção de caixa por ciclo de fatura: saldo previsto no vencimento, falta por ciclo, tipo de falta, dia do vencimento e data simulada. Fórmula completa em `regras-negocio.md`, "Projeção de caixa por ciclo". | Substitui o snapshot de `saldo_apos` (R3), a fonte do erro principal. Traz também o dia do vencimento (R18) e a data simulada (R19). |
| `silver_transacoes_resumo` (nova) | Compras no cartão da janela, por categoria e mês, com as compras parceladas agrupadas em "Parcelas". | Alimenta a pergunta "por que minha fatura veio tão alta?" (Spec, cenário C). |
| `gold_capacidade_pagamento` | Lê saldo, falta e tipo de falta de `silver_ciclo_fatura` em vez de `saldo_apos`. Nova coluna `dias_ate_recebimento`. | R3 e R16: a cobertura curta só vale se o próximo recebimento vier em até 25 dias depois do vencimento. |
| `gold_elegibilidade` | `ESTRUTURAL` virou `RECORRENTE`. | É o termo da Spec: falta que se repete. |
| `gold_contexto_agente` | Passou a ter exatamente as 19 colunas de `Contexto`, dinheiro em centavos (`INT64`). Saíram `saldo_atual`, `fatura_cabe`, `publico_mvp`, `acao_motor` e `recomendacao_motor`; renda, folga, fatura e falta passaram a ser `_c` (centavos). | Contrato das ferramentas. A recomendação agora é montada no Python a partir das flags do gold, das taxas e da liberação simulada. `acao_motor` continua em `gold_elegibilidade` para depuração. |

`silver_cliente_dia`, `silver_historico_fatura_12m` e `silver_cartao` não mudaram.

### Correção posterior (`aec39c4`)

Em `silver_ciclo_fatura`, o filtro `t.nom_cate_micro != 'Pagamento de fatura'` descartava as saídas com categoria nula (em SQL, `NULL != 'x'` não é verdadeiro), o que inflava o saldo previsto. Agora é `IFNULL(t.nom_cate_micro, '') != 'Pagamento de fatura'`. No mesmo commit, as colunas intermediárias com sufixo `_c` que estavam em reais foram renomeadas; a conversão para centavos acontece só no gold.

## O que ficou igual, de propósito

- **Segmentação por pedaladas em 12 meses:** reproduz 311/101/384/204.
- **Fatura estimada:** 1,33 × compras do mês anterior, a escolha da Spec para o protótipo. A fatura corrente ainda não foi paga, então a reconstrução exata pelo modo de pagamento não se aplica a ela.
- **Folga mensal = renda − recorrentes.** Continua superestimada: ignora o gasto variável, e `categoria_macro LIKE '%casa%'` conta toda a macro Casa como recorrente. Foi mantida por decisão e fica declarada como limitação. Como a folga é o teto de toda parcela, é a primeira coisa a revisar quando houver dados reais.
- **Padrões de descrição** de compra no cartão e de pagamento de fatura.

## Pendências

- **Rodar no BigQuery:** a carga (silver 01–08, gold 01–03) e as checagens em `sql/checks/`. Os números atuais de `homologacao.md` são da versão antiga ou da emulação local.
- **Distribuição do tipo de falta.** Na emulação local, a falta cresce com a severidade do grupo, como esperado:

  | Grupo | Sem falta | Recorrente | Pontual |
  |---|---:|---:|---:|
  | SEMPRE_QUITA | 96,1% | 1,9% | 1,9% |
  | ESCORREGAO | 88,1% | 8,9% | 3,0% |
  | ROLANDO_FATURA | 80,2% | 16,4% | 3,4% |
  | NO_LIMITE | 73,5% | 18,6% | 7,8% |

  Mas NO_LIMITE tem as maiores fatias de pontual e de recorrente, e só ~3% dos Escorregões têm falta pontual, que é a condição da cobertura curta. Isso basta para escolher uma persona de demo, mas indica que a janela de 3 ciclos é curta ou que a regra "≥ 2 de 3 ciclos" precisa de calibração com dados reais.
- **Ciclos pelas datas reais de pagamento.** Os ciclos usam as datas dos pagamentos de fatura, e não um calendário montado a partir do dia de vencimento. Na base sintética todo cliente tem exatamente 3 pagamentos na janela; com dados reais, clientes com 0–2 pagamentos caem nos valores de reserva documentados no SQL.
