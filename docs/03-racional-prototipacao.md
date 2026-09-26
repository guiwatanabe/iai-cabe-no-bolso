# 03 · Racional de prototipação (entregável)

Por que este produto, por que este recorte, o que foi descartado e por quê, o que é simulado. Complementa `02-proposta-produto.md` (o quê) e `04-arquitetura-gcp.md` (como).

## 1. Por que a fatura

A base sintética separa quatro perfis apertados de um saudável por um único sinal: pagamento de fatura parcial ou mínimo (98–100% contra 0%). Nenhum outro sinal separa. O pilar "construir o amanhã" quase não existe na base (nenhum investimento). O exemplo do case ("seu aluguel vence dia 10") descreve o perfil mais grave da base. Escolher a fatura é ler a base, não enviesá-la. O risco assumido: outras equipes podem chegar ao mesmo tema; a diferença precisa vir do que o agente faz, não do dado.

## 2. Por que um plano, e não uma oferta

O parcelamento de fatura já existe no app. O ia.i já responde perguntas sobre a fatura (teste da mentora: analisou os gastos e não sugeriu plano). Um agente que compara "mínimo ou parcelar" na hora de pagar é o que o mercado já tem. O que não encontramos em ninguém, até onde pesquisamos: um agente que assume o resultado (o cliente sair da fatura rolada), diagnostica a causa, monta um plano com data para acabar e volta a cada fatura. É isso que faz o produto ser agêntico no sentido que a mentora cobrou.

## 3. Por que urgência e causa, e não só urgência

A triagem A/B/C (reunião das 16h09) é boa: simples, determinística, fácil de explicar. Mas urgência não é causa. Consignado não existe para renda por PIX; cheque especial não resolve aposentado de renda fixa. A base tem quatro causas por perfil. Logo: o grupo diz a urgência, a causa diz o remédio, o produto é o plano, e o crédito é uma ferramenta dentro dele.

## 4. Por que o C não sai

Deixar o C fora exclui 83% da renda irregular e 53% dos aposentados. Impacto social é critério implícito. E "alguém rolando pela sexta vez" é exatamente quem a Lei 14.181/2021 (superendividamento) e a Resolução Conjunta nº 8 pedem que o banco trate com cuidado. O C entra sem crédito automático: renegociação, atendimento humano e plano para a causa.

## 5. Por que a conta do banco fecha

Rotativo com 65% de inadimplência (BC, julho de 2026) e juros limitados a 100% da dívida original (Lei 14.690/2023) é receita nominal, não recebida. Trocar por parcela paga com inadimplência perto da média das famílias (5,8%) aumenta o recebido esperado, reduz PDD e mantém o cliente. O simulador expõe cada variável (`config/taxas.yaml`) para que a banca troque os parâmetros e veja o resultado.

## 6. Alternativas descartadas

| Alternativa | Por que saiu |
|---|---|
| "Cabe no Mês": posso parcelar em 12x? | É o exemplo do próprio case; a fatura não fecha com as compras do cartão (só 6,1% batem) |
| Compra parcelada como gatilho | 99% da base parcela, com dor ou sem; parcelas iguais nos meses parciais e integrais |
| Gasto atípico como gatilho | Parcial no mês seguinte a uma oficina: 25,3% contra 26,5% na média |
| Cashback, Itaú Shop, gift card por categoria | Premia gasto no cartão, que é o que gera a fatura; perfil de gasto igual entre quem rola e quem não rola |
| Gamificação por gasto, missões | Sem evidência; vira ruído. Recompensa por comportamento (fatura inteira) fica para o futuro |
| Seguro prestamista como garantia | Venda casada (CDC); contra confiança e Res. 8 |
| Cheque especial para tirar débitos do cartão | Sem comparar custo, troca uma dívida por outra |
| Esquadra de vários agentes | Mais pontos de falha; os papéis viram capacidades de um agente com ferramentas |
| Calendário até o salário / prevenção de saldo negativo | `saldo_apos` não fecha (26,8%); PIX ambíguo; próximo do diagnóstico 360º do ia.i |
| Alerta estilo Defesa Civil, SMS no fechamento | Oposto do tom propositivo pedido; no fechamento o cliente ainda não decide |

## 7. O que é real e o que é simulado na demo

| Real (da base) | Simulado (declarado na tela) |
|---|---|
| Cliente `3e7d20b2`, seu extrato de 2025, salário e dia, contas fixas, gasto no cartão por categoria | A data de hoje |
| Fatura de cada mês reconstruída (`pago + juros/0,14`) | Taxa do produto de saída (parâmetro, fonte BC) |
| Modo de pagamento (integral/parcial/mínimo) e juros de cada mês | Elegibilidade ao consignado e ao crédito pessoal |
| Rotativo a 14% a.m. | Resposta do cliente sobre o PIX ou sobre um gasto atípico |
| Sequência de 4 faturas roladas (fev–mai) | Os meses seguintes com o plano aplicado |

## 8. Limites conhecidos

- A base não mostra qual alavanca funciona; o efeito do plano é hipótese do piloto.
- A base não carrega saldo entre faturas; a bola de neve é mecânica do mercado.
- Não sabemos a rubrica, o roadmap do ia.i para a fatura, nem custo de funding, perda em caso de calote e interchange do banco.
- Só cartões do próprio Itaú e correntistas no MVP.

## 9. Decisões (resumo; detalhe em `decisoes.md`)

1. Tema: fatura. 2. Produto: plano de saída acompanhado; crédito como ferramenta. 3. Triagem A/B/C + causa. 4. C incluído sem crédito automático. 5. Taxas como parâmetro; rotativo da base. 6. Um agente com ferramentas (ADK), não esquadra. 7. Demo: cliente B real, replay de 2025, meses seguintes simulados. 8. Sem seguro embutido, sem cashback no MVP.
