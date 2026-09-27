# Fluxo esperado por persona

Os valores abaixo saem das ferramentas reais (`contexto_fatura` sobre `tests/fixtures/`, com `CABE_DADOS=fixtures`) e das taxas de `mcp_server/taxas.yaml`. As personas são sintéticas, modeladas nos exemplos da Spec (Ana e Bruno), até as personas reais serem escolhidas no gold depois da carga no BigQuery. Os textos das conversas são o que o prompt pede; nenhuma resposta aqui foi gerada pelo Gemini.

[Fluxo por persona](fluxo-personas.excalidraw "width=1000 height=700")

## O que vale para todas

1. **Gatilho e sessão.** O front abre uma sessão por gatilho (`fechamento`, `pagar_outro_valor`, `pergunta_cliente`, `acompanhamento`) e modo (`insight` ou `conversa`) com `POST /sessao`. Consentimento e liberação de crédito simulada vão no mesmo corpo e viram estado da sessão no servidor. Dar ou revogar consentimento abre uma sessão nova.
2. **Contexto.** O agente sempre chama `contexto_fatura` antes de responder; se não chamar, o código força a chamada uma vez. `cliente_id` e o estado são injetados pelo código, nunca pelo modelo. Sem consentimento, a ferramenta devolve só a fatura.
3. **Números.** O modelo escreve cada valor como `[[fN]]`, e o código troca pelo valor formatado e monta `numeros_citados`. Não há como citar um número que a ferramenta não devolveu.
4. **Antes de sair.** Primeiro as checagens em código: formato por modo, oferta liberada, consentimento, tamanho e termos proibidos. Depois o validador (regras R1–R20 da Spec). Uma reprovação gera uma nova resposta; a segunda gera a mensagem segura; R8 (angústia, pedido de pessoa, fraude) vai direto para uma pessoa.
5. **O que o modelo nunca vê:** grupo do cliente, número de pedaladas, faturas abaixo do total.

## Ana: falta pontual, cobertura curta

Fixture `gold_escorregao.json`. Hoje simulado: 15/12/2025.

| Fato | Valor | Origem |
|---|---|---|
| Fatura / vencimento | R$ 1.700 / dia 20 | gold (1,33 × compras do mês anterior; moda do dia de pagamento) |
| Mínimo / juros e outros custos se pagar o mínimo | R$ 255 / R$ 202,30 | `core.fatura` + `taxas.yaml:rotativo` (15% e 14% a.m.) |
| Saldo previsto no vencimento | R$ 1.280 | gold (projeção de caixa por ciclo) |
| Falta prevista / tipo | R$ 420 / pontual | gold |
| Próximo recebimento | R$ 4.100, dia 5, 15 dias depois do vencimento | gold |
| Mudar vencimento | dia 6 ou dia 7 | `core.ofertas` |
| Oferta `cob_01`: cheque especial por 15 dias | custo total R$ 16,80 (8% a.m.) | `core.ofertas` + `taxas.yaml:cobertura_curta`, **taxa ilustrativa** |
| Ficar no rotativo por um mês | R$ 58,80 | `core.rotativo` |

**Cenário 1, preventivo (gatilho `fechamento`)**
1. Insight na aba de cartões (≤ 160 caracteres): "Ana, a fatura fechou em R$ 1.700. Até o dia 20, a previsão é ter R$ 1.280. Temos um jeito de pagar tudo." Botão "Ver opções" (`abrir_chat`).
2. Conversa: a falta é pontual e a troca de vencimento está disponível, então ela vem primeiro (Spec, regra 3a): vencimento no dia 6 ou 7, logo depois do salário. `acao: mudar_vencimento`.
3. Se Ana preferir resolver esta fatura: cobertura com cheque especial por 15 dias, custo total R$ 16,80, ao lado do custo de ficar no rotativo. `acao: mostrar_oferta`, `oferta_id: cob_01`.
4. "Quero pagar tudo": `acao: abrir_resumo_contrato`. Nada é contratado pelo agente; o resumo é do front.
5. Acompanhamento (gatilho `acompanhamento`, estado `oferta_aceita`): "aguardando" até o dia 5, depois "coberta"; se o salário atrasar, "atrasada" e nenhuma nova oferta de crédito.

**Cenário 3, prefere o mínimo:** o agente informa uma vez os juros e outros custos do próximo mês (R$ 202,30) e respeita a escolha. `acao: nenhuma`.

## Bruno: falta recorrente, crédito consignado

Fixture `gold_rolando.json`. Hoje simulado: 10/12/2025.

| Fato | Valor | Origem |
|---|---|---|
| Fatura / vencimento | R$ 1.850 / dia 15 | gold |
| Mínimo / juros e outros custos se pagar o mínimo | R$ 277,50 / R$ 220,15 | `core.fatura` + `taxas.yaml:rotativo` |
| Saldo previsto no vencimento | R$ 1.100 | gold |
| Falta prevista / tipo | R$ 750 / recorrente | gold |
| Parcelas em curso no mês | R$ 420 | gold |
| Folga mensal | R$ 600 | gold (renda − recorrentes; superestimada, ver `sql/docs/mudancas.md`) |
| Oferta `cons_01`: crédito consignado | 10 parcelas de R$ 90,18, custo total R$ 151,80 (3,5% a.m.) | `core.ofertas` + `taxas.yaml:credito_consignado`, **taxa a conferir** |
| Crédito com seguro prestamista | não aparece | taxa desconhecida em `taxas.yaml`, então o produto não existe para o agente |
| Ficar no rotativo por um mês | R$ 105 | `core.rotativo` |

**Cenário 2, reativo (gatilho `pagar_outro_valor`, antes de confirmar o pagamento abaixo do total)**
1. Insight: "Antes de confirmar: tem um jeito de pagar a fatura inteira com um crédito de parcelas que cabem no seu mês." Botões "Ver opção" e "Continuar com este valor". Sem oferta liberada, esse gatilho não gera texto e o cliente segue direto para a confirmação.
2. Conversa: começa pelo que pesa no mês (parcelas em curso de R$ 420), sem citar o histórico de pagamentos. Propõe o crédito com consignado: 10 parcelas de R$ 90,18, custo total R$ 151,80, dizendo que é um empréstimo e não o parcelamento da fatura. `acao: mostrar_oferta`, `oferta_id: cons_01`.
3. Aceite: `abrir_resumo_contrato`. Se Bruno fosse aposentado (`publico_vulneravel`), a ação seria `transferir_humano`.
4. Acompanhamento um mês depois: parcelas pagas, próxima parcela e limite do cartão liberado, sem nova oferta.

**Cenário 5, a parcela não cabe:** com folga menor que a menor parcela possível (por exemplo, folga de R$ 40 contra 24 parcelas de R$ 46,70), `ofertas_liberadas` vem vazia. O agente mostra as formas de pagar e oferece falar com alguém da equipe, sem mencionar crédito.

## Carla: fora do escopo de crédito

Fixture `gold_no_limite.json`: 7 pedaladas em 12 meses e renda irregular (confiança BAIXA). Fatura de R$ 1.400, vence dia 8. `ofertas_liberadas` vem vazia por duas regras de código (6+ pedaladas; confiança diferente de ALTA). O agente mostra as formas de pagar e oferece uma pessoa (Spec, exemplo C11), sem falar em crédito.

## Sem consentimento (qualquer persona)

`contexto_fatura` devolve só a fatura: valor, vencimento, mínimo, juros se pagar o mínimo e o parcelamento da fatura. O agente mostra as formas de pagar e diz que o cliente pode ativar a análise (Spec, exemplo C8). Se o modelo tentar mostrar uma oferta, a checagem em código troca a resposta pela mensagem segura.
