# 10 · Contrato das tabelas gold (o que o agente consulta)

Combinado na chamada de 26/09 à noite: camada medallion no BigQuery (Lucas). Bronze = extrato bruto dos 90 dias (o "cache" do app). Silver = tratamento por dia, com a coluna nova de faturas roladas nos últimos 12 meses. Gold = agregados prontos; o agente só faz `SELECT` por `id_usuario`, uma consulta por tabela por sessão, nunca em loop.

Em dev e nos testes o mesmo cálculo roda em Python sobre o CSV (`agent/cabe_core`), com a mesma interface. Se o BigQuery falhar na demo, o CSV assume. Por isso as definições abaixo precisam ser as mesmas nos dois lados.

## Convenções

- Projeto `batalha-time-05-xew3`, região `us-central1`, dataset sugerido `cabe_no_bolso` (se criar dataset for bloqueado, usar `hackathon_dados` com prefixo `gold_`).
- Dinheiro em **centavos, inteiro** (`round(vlr * 100)`). Mês como `anomes` inteiro (AAAAMM). Uma linha por cliente e mês nas tabelas mensais. Particionar por `anomes`, agrupar por `id_usuario`.
- **Rolar a fatura** = `nom_cate_micro = 'Pagamento de fatura'` com `descr` contendo `minimo` ou `parcial`. Integral é o resto.
- **Fatura exata** (docs/01 §3): integral → `pago`; mínimo → `pago / 0,15`; parcial → `pago + juros / 0,14`. `juros` = soma de `Juros pagos` no mês. Arredondar ao centavo. **Não usar 1,33 × compras para a fatura do mês**: o fator só projeta faturas futuras (docs/01 §9).
- **Renda recorrente** = mediana das entradas de `Salarios e bonificacoes` e `Beneficio INSS` nos 3 meses da janela. Entradas de `Recebimentos diversos` (PIX) e restituição são **esporádicas**: viram flag, não renda.
- **Fixos** (lista de `analise/persona_b.py`): Mensalidade escolar, Condominio, Seguro de automovel, Energia eletrica, Agua e esgoto, Gas, TV Internet celular e telefone, Celular, Emprestimos, Outros emprestimos, Consorcio, Pagamento de aluguel, Financiamento de imovel.
- **Essenciais** (macros): Mercado, Posto de combustivel, Transporte publico, Cuidados pessoais, Educacao, Pets, Casa.
- **Cartão** = `descr` começa com `cart credito`. Compra no cartão e pagamento da fatura são ambos saída: não somar os dois.
- **Folga do mês** = renda recorrente − fixos − essenciais. **Falta** = fatura − folga (≤ 0 significa que cabe).
- **Janela** = os 3 meses fechados antes do mês da fatura, mais o mês da fatura.

## Tabelas

### `gold_fatura_mes` — uma linha por cliente e mês com pagamento de fatura

| Coluna | Tipo | Definição |
|---|---|---|
| `id_usuario` | STRING | hash do cliente |
| `anomes` | INT64 | mês do pagamento |
| `fatura` | INT64 | fatura exata, centavos |
| `pago` | INT64 | valor pago no mês |
| `modo` | STRING | `integral` / `parcial` / `minimo` |
| `juros` | INT64 | `Juros pagos` do mês (rotativo nos meses rolados; cheque especial nos integrais) |
| `nao_pago` | INT64 | `fatura − pago` |
| `dia_vencimento` | INT64 | dia do pagamento da fatura |
| `compras_mes` | INT64 | compras no cartão no mês (base da projeção da fatura seguinte) |

### `gold_cliente_mes` — uma linha por cliente e mês

| Coluna | Tipo | Definição |
|---|---|---|
| `id_usuario`, `anomes` | | |
| `renda_total` | INT64 | todas as entradas (`tipo = E`) |
| `renda_recorrente` | INT64 | salário + INSS do mês |
| `entradas_esporadicas` | INT64 | PIX recebido e afins |
| `dia_recebimento` | INT64 | dia do salário ou benefício |
| `fixos` | INT64 | lista acima |
| `essenciais` | INT64 | macros acima |
| `cartao` | INT64 | compras no cartão |
| `delivery_app` | INT64 | macros Delivery + Transporte por app |
| `folga` | INT64 | renda_recorrente − fixos − essenciais |

### `gold_perfil` — uma linha por cliente (a "ficha")

| Coluna | Tipo | Definição |
|---|---|---|
| `id_usuario` | STRING | |
| `perfil` | STRING | `CLT+fin`, `CLT s/fin`, `INSS`, `PIX+aluguel`, `CLT+fin+recebe` (regra de `analise/grupos_abc.py`) |
| `dia_salario` | INT64 | mediana do dia do salário |
| `salario_mediano` | INT64 | |
| `fatura_mediana` | INT64 | |
| `roladas_12m` | INT64 | faturas roladas nos 12 meses **anteriores** ao mês de referência (a coluna nova da silver) |
| `roladas_seguidas` | INT64 | maior sequência |
| `grupo` | STRING | `em_dia` (0), `escorregao` (1–2), `rolando` (3–5), `no_limite` (6+) |
| `parcelamentos_12m` | INT64 | parcelamentos contratados pelo Cabe no Bolso nos últimos 12 meses (0 na base; trava de 1×/12 meses) |
| `conta_voltou_positivo` | BOOL | desde o último uso de cheque especial (simulado `true` na base) |

Se a ficha for por mês de referência (melhor para a demo, que simula datas), acrescentar `anomes_ref` e calcular `roladas_12m` só com meses anteriores a ele. É assim que o CSV faz.

### `gold_anomalia` — uma linha por cliente e mês (roda dentro do motor de capacidade)

| Coluna | Tipo | Definição |
|---|---|---|
| `id_usuario`, `anomes` | | |
| `renda_irregular` | BOOL | recebimento recorrente não previsível na janela |
| `entradas_esporadicas` | JSON | `[{"anomes":202508,"descr":"...","valor":123456}]` |
| `gastos_atipicos` | JSON | `[{"categoria":"Lazer","valor":..., "mediana":..., "desvio_pct":...}]` |
| `metodo` | STRING | `media_movel`, `lag`, ou o vencedor das três metodologias |
| `pressure_score` | FLOAT64 | Financial Pressure Score (documentar a fórmula no README de dados) |

### `gold_transacoes_90d` (opcional) — para "por que minha fatura veio tão alta?"

`id_usuario`, `anomesdia`, `tipo`, `descr`, `valor` (centavos), `nom_cate_macro`, `nom_cate_micro`. Só os 90 dias. Nunca vai inteira para o prompt: uma ferramenta agrega as maiores categorias e devolve 5 linhas.

## O que o agente faz com isso (para conferir o DOD)

1. `gold_perfil` → grupo e travas (1×/12 meses, no_limite sem crédito).
2. `gold_fatura_mes` (mês atual + 12 anteriores) → fatura exata, mínimo (15%), histórico de modos.
3. `gold_cliente_mes` (janela) → renda recorrente, fixos, essenciais, folga, falta, dias até o recebimento.
4. `gold_anomalia` (mês atual) → flags que vão para o prompt como texto, nunca como número solto.
5. Projeção das próximas 3 faturas = `fator_projecao_fatura` (1,33) × `compras_mes`.

Personas da demo: `755627ab-804b-4211-b0ea-f4ebacc58716` em `202508` (Escorregão, "Ana") e `3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b` em `202509` (Rolando, "Bruno"). O JSON `data/personas/3e7d20b2_grupo_b.json` é o gabarito: se a gold bater com ele mês a mês, o agente e o BigQuery estão falando a mesma língua.
