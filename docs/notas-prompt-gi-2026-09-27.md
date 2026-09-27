<!-- Texto extraído de docs/notas-prompt-gi-2026-09-27.pdf (Gi, 27/09/2026). Fonte de verdade é o PDF. -->

Notas do agente Cabe no Bolso
 Sep 27, 2026  ·  @GERSON
Arquitetura: um agente, dois modos, um validador
Um único agente faz o texto rápido da aba de cartões e a conversa no chat do ia.i. O
validador é um segundo agente, separado.
Por que um agente só para os dois usos. O insight e a conversa usam os mesmos dados,
as mesmas regras e o mesmo tom. O chat continua de onde o insight parou: se o card diz
"a previsão é ter R$ 1.280", o chat precisa dizer o mesmo número. Dois agentes com dois
prompts tendem a divergir com o tempo. O modo é um campo do contexto: insight  ou
conversa .
Por que o validador é separado. Um agente não deve corrigir a própria saída: ele tende a
concordar consigo mesmo. O validador recebe o mesmo contexto e a mensagem pronta, e
só responde se aprova ou não, com o motivo. Ele não conversa com o cliente.
arquitetura · 1 agente com 2 modos e 1 validador
Notas do agente Cabe no Bolso
Page 1 of 4

Antes do validador, código. O que dá para checar sem IA é checado em código: números
citados, oferta liberada, tamanho do texto. O validador cuida do que exige leitura: tom,
proibições, sinais de risco.
Como usar o prompt e os exemplos
O documento de prompt tem três blocos: o prompt do agente, os exemplos e o prompt do
validador.
Prompt do agente: é o prompt de sistema para os dois modos. O contexto entra em
{{contexto_json}} .
Exemplos: substituem {{exemplos}}  no fim do prompt do agente. São 6 de insight
para a aba de cartões e 16 de conversa, cada um com o contexto resumido e a saída
esperada. O caso da Ana é a referência de tom.
Prompt do validador: recebe o contexto, as últimas mensagens e a saída do agente, e
devolve aprovado ou reprovado com o motivo. Ele nunca escreve para o cliente.
Os valores das ofertas nos exemplos (R$ 38, R$ 310, 12 parcelas) são ilustrativos. Em
produção, vêm do serviço de crédito.
O que acontece depois da validação
Toda mensagem reprovada tem no máximo uma segunda chance; depois disso, entra um
texto fixo.
1. Aprovado: a mensagem vai para a aba de cartões ou para o chat.
2. Reprovado: o agente gera de novo uma única vez, recebendo as violações e a
orientação.
3. Reprovado de novo, ou qualquer violação de R8: entra a mensagem segura, um texto
fixo aprovado pelo jurídico. No insight: "Sua fatura fechou em R$ [valor] e vence dia
[dia]. Veja as formas de pagar." No chat, a mesma frase, mais a oferta de falar com
alguém da equipe. Com R8, a conversa vai direto para uma pessoa.
4. Registro: toda reprovação fica gravada com a regra violada, para ajustar o prompt no
piloto.
Checagens em código antes do validador
O que tem resposta certa ou errada é checado em código, antes do validador. Assim, o
validador gasta atenção só com o que exige leitura.
Notas do agente Cabe no Bolso
Page 2 of 4

Exemplo de contexto de entrada
Os números chegam já formatados como texto ("R$ 1.700", "dia 20"). Isso evita que a LLM
formate ou arredonde valores e facilita a checagem de números em código.
{
  "modo": "conversa",
  "gatilho": "fechamento",
  "consentimento": true,
  "cliente": {"primeiro_nome": "Ana"},
  "fatura": {
    "valor": "R$ 1.700",
    "vencimento": "dia 20",
    "pagamento_minimo": "R$ 255",
    "formas_de_pagar": ["total", "parcelamento da fatura", "mínimo"]
  },
  "capacidade": {
    "saldo_previsto_no_vencimento": "R$ 1.280",
    "proximo_recebimento": {"valor": "R$ 4.100", "data": "dia 5"},
    "folga_mensal": "R$ 350",
    "falta_prevista": "R$ 420",
Checagem Regra Se falhar
JSON válido A saída segue o formato do modo Regenera
Números Cada item de numeros_citados existe
no contexto, e todo número do texto
está em numeros_citados
Regenera; na segunda falha,
mensagem segura
Oferta oferta_id existe em ofertas_liberadas;
se a lista está vazia, oferta_id é nulo
Mensagem segura
Consentimento Sem consentimento, a ação não é
mostrar_oferta nem
abrir_resumo_contrato
Mensagem segura
Tamanho Insight: só texto, sem título, até 160
caracteres; conversa: até 3 mensagens
Regenera
Termos
proibidos
Lista fixa de palavras (score,
Escorregão, pedalada e as demais do
prompt)
Regenera
Limite de
contato
No máximo uma mensagem proativa
por dia para o mesmo cliente
Não envia
Notas do agente Cabe no Bolso
Page 3 of 4

    "tipo_de_falta": "pontual"
  },
  "parcelas_em_curso": "R$ 0",
  "ofertas_liberadas": [
    {"id": "cob_01", "tipo": "cobertura_cheque_especial",
     "prazo": "15 dias", "custo_total": "R$ 38", "cet": "[taxa]"}
  ],
  "mudar_vencimento": {"disponivel": false, "datas": []},
  "transacoes_90d": "[resumo por categoria e mês]",
  "historico_conversa": []
}
Para validar com tech
Montagem do contexto: quem gera o JSON de entrada, de onde vem cada campo
(conta, cartões, serviço de crédito) e se os números podem chegar já formatados como
texto.
Campos de 12 meses, fora da janela de 90 dias do prompt: credito_usado_12m (crédito
com consignado ou seguro prestamista contratado pelo Cabe no Bolso) e
faturas_abaixo_do_total_12m (quantas faturas o cliente pagou abaixo do total).
Origem do grupo do cliente com a janela de 90 dias.
Campos novos do contexto: fatura.encargos_se_pagar_minimo,
cliente.publico_vulneravel, reincidencia_pos_credito, amplia_limite e novo_limite nas
ofertas de cobertura, e o bloco acompanhamento (status da cobertura e do crédito).
Ações novas para o front executar: registrar_permissao_ampliacao e
revogar_consentimento, além do gatilho acompanhamento no orquestrador do ia.i.
Notas do agente Cabe no Bolso
Page 4 of 4