# 07 · Roteiro da demo e do pitch

> **Aviso (27/09, madrugada).** A jornada e as personas da demo seguem a **spec da Gi** (`docs/spec-gi-2026-09-27.pdf`) e o **PRD 1.0** (`docs/09-prd.html` §7): jornada em 9 passos com gatilho na escolha de pagar abaixo do total e consentimento antes do motor; personas **Ana · Escorregão** (`755627ab-804b-4211-b0ea-f4ebacc58716`, ago/2025, fatura R$ 2.955,00, cobertura curta de 7 dias) e **Bruno · Rolando a fatura** (`3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b`, set/2025, fatura R$ 3.619,95, consignado 10× R$ 110,51); pitch de **4 minutos** em 5 blocos. Os números que a demo mostra saem de `agent/cabe_core` e estão em `demo/mock/*.json` e no `README.md` da raiz. O roteiro abaixo (data simulada 20/02/2025, persona única, ~3 minutos) é o histórico da versão de 26/09 e foi substituído; `docs/decisoes.md` registra a troca (pendência 4).

Formato pedido pela staff: algo navegável, com QR code, que a banca usa no celular enquanto o time apresenta (~3 minutos). Tudo que é simulado aparece na tela.

## Persona (cliente real da base)

`3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b` · CLT sem financiamento · grupo B · `data/personas/3e7d20b2_grupo_b.json`

- Salário R$ 3.787 no dia 7. Fatura vence dia 20. Contas fixas ~R$ 929 por mês.
- Janeiro e fevereiro: cartão de R$ 7.045 e R$ 9.317 (compra grande em lazer; R$ 13.530 em lazer no ano). Delivery e transporte por app: ~R$ 748 por mês, 20% da renda.
- Fevereiro: fatura de R$ 7.612 (2 vezes a renda); pagou o mínimo, R$ 1.142; R$ 906 de juros.
- Março a maio: pagou parcial; juros de R$ 320, R$ 118 e R$ 263. Em março pagou R$ 7.147 de uma vez (dinheiro que não veio do salário; provável limite da conta).
- Ano: 5 faturas roladas, 4 seguidas, R$ 2.373 de juros (5,2% da renda anual). Meses seguintes com juros de conta (cheque especial) mesmo pagando a fatura inteira.

Causa que o agente diagnostica: gasto atípico em jan–fev sobre um dia a dia já pressionado (delivery e app).

## Jornada na tela (data simulada: 20/02/2025)

1. **Tela "Pagar fatura"** — total R$ 7.612 · mínimo R$ 1.142 · parcelar. A banca toca em "pagar o mínimo".
2. **Sinal** — o agente aparece no painel: "Sua fatura de fevereiro ficou em R$ 7.612, duas vezes o que entra no mês. Se pagar o mínimo, os juros do cartão ficam em torno de R$ 906 só este mês. Quer que eu monte uma saída que caiba no seu bolso?" (números da base: 14% sobre o não pago).
3. **Consentimento** — "Preciso olhar seu extrato dos últimos 12 meses: salário, contas fixas e cartão. Posso?" → "Pode olhar".
4. **Diagnóstico** — cartão com a causa e três evidências: compra grande em lazer (jan–fev), delivery e app em 20% da renda, salário de R$ 3.787 no dia 7 com R$ 929 de contas fixas. Urgência: primeira vez no ano (o plano evita que vire B/C). Observação: para a demo, a triagem mostra "1ª fatura rolada; risco alto pelo tamanho".
5. **Pergunta certa** — "Essa compra foi planejada? Tem alguma entrada extra prevista?" A banca responde (simulado).
6. **Duas saídas** (taxas ilustrativas de `config/taxas.yaml`, com fonte):

| | Se continuar como está | Saída recomendada | Alternativa |
|---|---|---|---|
| Hoje | paga o mínimo R$ 1.142 | paga R$ 2.000 | paga R$ 2.000 |
| O que ficou para trás | R$ 6.470 a 14% a.m. | R$ 5.612 em 10 parcelas fixas | R$ 5.612 em 6 parcelas fixas |
| Parcela | variável, cresce | ~R$ 675 (consignado CLT, 3,5% a.m., ilustrativo) ou ~R$ 762 (crédito pessoal, 6% a.m.) | ~R$ 1.053 ou ~R$ 1.141 |
| Custo total de juros | R$ 1.607 em 4 meses (o que aconteceu) e ainda devendo | ~R$ 1.136 a R$ 2.013 | ~R$ 707 a R$ 1.236 |
| Termina em | sem data | dez/2025 | ago/2025 |
| Cabe no mês? | não | sim (parcela ≤ sobra livre) | não (parcela > sobra livre) |

   O agente marca a recomendada, diz por quê ("é a maior parcela que ainda deixa R$ 900 para o cartão até a próxima fatura") e mostra o teto do mês.
7. **Confirmação** — "Fica assim: R$ 2.000 agora, 10 parcelas de ~R$ 675, e até dia 20 cabem R$ 900 no cartão. Confirmo?" → ok. Nada é contratado.
8. **Avançar mês** (botão da demo) — março: fatura paga inteira, "uma de três". Abril: inteira, "duas de três". Maio: inteira, plano encerrado; comemoração sóbria; comparação com o que aconteceu de verdade em 2025 (R$ 2.373 de juros, 5 faturas roladas).
9. **"Como cheguei aqui"** — painel para a banca: cada ferramenta chamada, cada número e sua origem, o que foi simulado.

Rodapé fixo: "Protótipo do Time 05. Data, taxas, elegibilidade e meses seguintes são simulados. Nenhum pagamento ou contratação real."

## O que é real e o que é simulado

Real: o cliente, o extrato, as faturas reconstruídas, os juros, os modos de pagamento, o rotativo de 14%. Simulado: a data, as taxas dos produtos de saída, a elegibilidade, as respostas da banca, os meses com o plano aplicado.

## Pitch (5 slides, ~3 minutos)

1. **A dor (30s):** "Quanto mais apertado eu fico, mais caro fica sair do aperto." 69% da base rola a fatura; quatro causas; este cliente, mês a mês.
2. **O problema de design (30s):** rotativo com 65% de inadimplência (BC, julho de 2026). O cliente perde e o banco também.
3. **Cabe no Bolso (60s):** sinal, causa, urgência, plano com data para acabar, teto do mês, acompanhamento até 3 faturas inteiras. "Aponte o celular: a demo está no QR code."
4. **A conta fecha (30s):** cliente paga menos e sabe quando termina; banco troca juro não recebido por parcela paga, B não vira C, cliente fica. Fórmula com variáveis abertas.
5. **Piloto e responsabilidade (30s):** 384 clientes do grupo B; métricas; nenhum número sai do modelo, consentimento antes, humano sempre, sem produto fora do plano. MVP e futuro.

## Plano B da demo

Se o Cloud Run falhar: a mesma demo web roda localmente e é exibida por espelhamento; o QR aponta para um túnel ou para a versão estática com respostas gravadas. Gravar um vídeo de 60 segundos da jornada como último recurso.
