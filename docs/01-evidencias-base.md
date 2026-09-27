# 01 · Evidências da base

Base: `data/extrato_sintetico.csv.gz` (BigQuery `batalha-time-05-xew3.hackathon_dados.extrato_sintetico`, us-central1). 467.585 lançamentos, 1.000 clientes, 01/01/2025 a 31/12/2025, 11 colunas. SHA-256 `d358f827c21d76ab7af524d7c0ea12dd1b62f1c7f971c25f33472042edafd1a9`. Dicionário e cuidados em `data/README.md`. Scripts em `analise/` (cada número abaixo indica o script que o gera).

Marcação: **[F]** fato medido na base · **[I]** inferência · **[H]** hipótese · **[D]** desconhecido.

## 1. Os cinco perfis (`arquetipos.py`, `varredura.py`)

| Perfil | Clientes | Rolam a fatura | Meses no ano (mediana) | Maior sequência seguida (mediana) | Juros no ano | Causa marcada [I] |
|---|---:|---:|---:|---:|---:|---|
| CLT com financiamento imobiliário | 400 | 390 | 4 | 2 | R$ 255.870 | Renda comprometida: contas fixas levam 58,5% da renda; 13º em todos, PLR em 80% |
| CLT sem financiamento nem aluguel | 100 | 99 | 5 | 2 | R$ 127.014 | Gasto do dia a dia: delivery e app em 100% (30–48% nos outros); 46% da renda no cartão; 8 meses com fluxo negativo |
| Aposentado INSS | 100 | 100 | 6 | 3 | R$ 44.811 | Sem causa clara nos dados: renda estável (variação 0,03), fluxo negativo em só 2 meses |
| Renda só por PIX e paga aluguel (dia 8, ~R$ 1.507) | 100 | 100 | 7 | 4 (máx. 10) | R$ 113.497 | Renda irregular: variação mensal 0,30 (CLT 0,18) |
| CLT com financiamento que recebe aluguel | 300 | 0 | 0 | 0 | R$ 0 | Saudável; se distingue por aluguel recebido e PLR |

[F] "Rolar a fatura" = pagamento de fatura com descrição `parcial` ou `minimo`. A base rotula cada pagamento como integral, parcial ou mínimo.

[I] A base foi montada para contar a história da fatura: o sinal separa os perfis de forma limpa (98–100% nos quatro perfis apertados, 0% no saudável). Nenhum outro sinal separa tão bem. Outras equipes podem chegar ao mesmo tema.

## 2. Sinais que não contam história (`varredura.py`)

[F] Aparecem na mesma proporção em todos os perfis, inclusive no saudável: empréstimo pessoal (60–69% dos clientes), renegociação (14–23%), consignado (9–13%), seguros (63–68%), capitalização (~30%), consórcio (26–31%), multa por atraso (23–32%), saque (36–47%), tarifas e assinaturas (100%), compra parcelada (99–100%). São textura da base, não causa.

[F] "Construir o amanhã" quase não existe: nenhuma aplicação, poupança ou investimento; 2 lançamentos `pix transf reserva` em 467 mil.

## 3. Juros decodificados (`juros.py`, `juros2.py`)

[F] **Rotativo a 14% ao mês.** No pagamento mínimo, o mínimo é 15% da fatura e os juros do mês são exatamente 14% do que ficou sem pagar (p10 e mediana da razão = 0,140). No parcial, a fração paga fica entre 50% e 80% (mediana 63%) e os juros seguem os mesmos 14% do não pago.

[F] **Reconstrução da fatura:** no parcial, `fatura = pago + juros / 0,14`; no mínimo, `fatura = pago / 0,15`; na fatura inteira, `fatura = pago`, porque os juros desses meses são de cheque especial (parágrafo abaixo). Isso permite a fatura real de qualquer mês, sem inventar valor.

[F] **"Juros pagos" mistura dois produtos** com os mesmos rótulos (`debito conta juros lim`, `debito conta juros saldo dev`): rotativo do cartão nos meses de pagamento parcial ou mínimo (R$ 447 mil) e cheque especial nos 1.104 meses de fatura paga inteira com juros (R$ 95 mil). Nesses meses, 100% têm saldo negativo e o juro é ~1,3% do saldo negativo mínimo do mês (mediana 0,0132; correlação −0,99).

[F] **A base não carrega o saldo não pago para a fatura seguinte.** A fatura do mês seguinte fica igual à mediana do cliente depois de mês inteiro, parcial ou mínimo (0,98–0,99), sem relação com o valor não pago (correlação −0,001). A bola de neve de juros compostos existe no mercado, mas não está modelada na base. No pitch, apresentar como mecânica do mercado, não como dado nosso.

## 4. Grupos por urgência (`grupos_abc.py`)

Critério do time (reunião das 16h09): meses no ano pagando menos que o total.

| Grupo | Clientes | Juros no ano | Mediana por cliente | Composição |
|---|---:|---:|---:|---|
| A: 1–2 meses | 101 | R$ 50.186 | R$ 246 | 91 CLT+financ, 8 CLT s/ financ, 2 INSS |
| B: 3–5 meses | 384 | R$ 285.267 | R$ 574 | 253 CLT+financ, 69 CLT s/ financ, 45 INSS, 17 PIX+aluguel |
| C: 6+ meses | 204 | R$ 202.889 | R$ 877 | 83 PIX+aluguel, 53 INSS, 46 CLT+financ, 22 CLT s/ financ |
| Nunca | 311 | R$ 0 | R$ 0 | 300 saudáveis + 11 |

[F] Sequências: 486 clientes rolaram 2 meses seguidos ou mais; 245 (24,5%) rolaram 3 ou mais; 111 rolaram 4 ou mais.

[I] Deixar o C fora exclui 83% da renda irregular e 53% dos aposentados, os mais vulneráveis.

## 5. Impacto (`impacto.py`)

- [F] 689 clientes (68,9%); 3.177 pagamentos parciais ou mínimos (26,5% dos 12.000); R$ 447 mil de juros de rotativo no ano.
- [F] Juros por cliente são pequenos: mediana de R$ 32 a R$ 91 por mês, 0,5% a 2% da renda. O que pesa é a repetição.
- [F] Juros evitáveis se o cliente pagasse a fatura inteira sempre que a sobra do mês cobria a fatura típica: R$ 60 mil (sem contar PIX recebido; 293 clientes) a R$ 229 mil (contando PIX; 588 clientes).
- [F] 452 meses com 13º ou PLR em que o cliente ainda rolou a fatura (323 clientes, R$ 70 mil).
- [D] Efeito para o banco (PDD, retenção): não medível nesta base.

## 6. O que a base não prova (`alavancas.py`, `solucao_dados.py`)

- [F] Dentro de cada perfil, a correlação entre meses rolados e gasto no cartão ou contas fixas fica perto de zero (−0,05 a 0,10). A correlação de 0,37 aparece só misturando perfis.
- [F] A sobra do mês não explica o pagamento parcial: mediana de R$ 1.990 nos meses parciais contra R$ 1.978 nos integrais. Com o PIX recebido contado como disponível, 60,2% dos meses parciais tinham sobra para a fatura típica; sem ele, 17,5%.
- [F] O dia do vencimento não muda a taxa (22% a 24% em qualquer data).
- [F] Persistência fraca: pagar parcial num mês leva a 41,8% de chance de repetir no seguinte, contra 36,4% depois de um mês inteiro (entre os 689).
- [H] Qual alavanca tira o cliente do comportamento é hipótese para o piloto. A base mostra quem, quanto e com qual causa.

## 7. Números errados que circularam (não usar)

| Circulou | Problema | Use |
|---|---|---|
| 94% fecharam algum mês negativo | Soma compra no cartão e pagamento da fatura (dupla contagem) | 829 clientes (83%) |
| 18,7% dos gastos em produtos financeiros "são juros" | 91% da categoria é o próprio pagamento da fatura | Juros = R$ 541 mil, 0,5% das saídas |
| 24% pagaram parcial ao menos uma vez | Contou operações, não clientes | 689 clientes (69%); 24,5% rolaram 3 meses seguidos ou mais |
| Renda por PIX: 7 meses seguidos | São 7 meses no ano | Sequência mediana de 4, máximo 10 |
| 29.847 parcelados; 56.141 com saldo negativo | São lançamentos, não clientes; `saldo_apos` só fecha em 26,8% das transições | 327 clientes com saldo negativo em algum lançamento |

## 8. Personas exportadas (`persona_b.py`, `persona.py`)

- **Grupo B, CLT sem financiamento:** `3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b` (`data/personas/3e7d20b2_grupo_b.json`). Salário R$ 3.787 no dia 7; fatura dia 20; contas fixas ~R$ 929; rolou 5 faturas, 4 seguidas (fev–mai); R$ 2.373 de juros no ano (5,2% da renda anual). Gasto atípico em jan–fev (cartão R$ 7.045 e R$ 9.317, Lazer R$ 13.530 no ano) e delivery+app ~R$ 748 por mês. Persona da demo.
- **Grupo C, CLT com financiamento:** `0a37f67b-77d5-4ab8-af28-2d88aaa252f1`. Salário R$ 5.332 (dia 7), financiamento R$ 3.814 (dia 8), escola R$ 673 (dia 11), fatura dia 15; rolou 9 de 12; R$ 1.047 de juros. Serve para mostrar o C.

## 9. Conferência da Spec da Gi (27/09, `confere_spec_gi.py`)

- [F] Fator 1,33: a mediana de fatura ÷ compras no cartão do mês anterior é 1,33 nos meses integrais. Nos meses rolados, 1,33 × compras erra a fatura exata em 18% na mediana (mais de 20% em 46% dos meses). Serve para o agregado e para projetar faturas futuras, não para a fatura de um cliente. Persona `3e7d20b2`: fevereiro exato R$ 7.612 contra ~R$ 9.370 pelo fator; março exato R$ 9.433 contra ~R$ 12.391.
- [F] Não pago no ano pela reconstrução exata: A 7,9% da fatura (R$ 173 mil); B 18,3% (R$ 1,55 milhão); A+B R$ 1,72 milhão. Fatura paga no vencimento: A 92,1%, B 81,7%. A spec, pelo fator, tem 7,4% / 19,9% / ~R$ 1,7 milhão / 92,6% / 80,1%: agregado próximo.
- [F] Os R$ 246 / R$ 574 / R$ 877 por cliente (A/B/C) são todos os "Juros pagos" do ano, não só cheque especial. Rotativo: R$ 177 / R$ 514 / R$ 858 na mediana. Juros de cheque especial em mês de fatura inteira aparecem em 42% dos clientes do A, 37% do B e 21% do C; a mediana por cliente é zero.
- [F] Gasto no cartão por cliente no ano (mediana): nunca rola R$ 15.038, A R$ 14.054, B R$ 16.407, C R$ 15.105. Parecido entre grupos, como diz a spec (que cita R$ 15,8–17,3 mil com outra definição).
- [F] A distância entre o dia do salário e o vencimento não muda a taxa de fatura rolada no A+B: 27% a 30% em qualquer faixa (5–10, 10–15, 15–20, 20+ dias). Mudar o vencimento não tem evidência de resolver a causa; fica como opção, não como argumento.
