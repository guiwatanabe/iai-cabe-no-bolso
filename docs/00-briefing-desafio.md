# 00 · O que o desafio pediu

Fontes: case oficial (`fontes/oficiais/case-oficial.pdf`, p. 5–7), transcrição automática da abertura de 26/09/2026 às 8h (Granola), mentoria das 14h10 com Jack (gerente de engenharia, crédito PF) e avisos da staff. Transcrições são automáticas: falas podem estar imprecisas; a atribuição de quem falou não é confiável.

## O case (texto oficial)

Desenvolver um produto baseado em agentes que ajude clientes a gerir melhor suas finanças, transformando dados e contexto em orientação útil para melhores decisões. Base sintética com extratos de mil pessoas.

Premissas: (a) seguir a Resolução Conjunta nº 8 do BACEN/CMN sobre educação financeira; (b) considerar uma instituição financeira consolidada, com produtos disponíveis, atuando junto ao público brasileiro; (c) usar ferramentas Google.

Três pilares: organizar o hoje (renda e gastos, orçamento, consumo, objetivos), construir o amanhã (reserva, investimentos, patrimônio, futuro), usar o crédito a seu favor (cartões, empréstimos e financiamentos, dívidas, capacidade de pagamento). Três ações da IA: entender, antecipar, orientar.

O exemplo didático do case: "Posso parcelar esse celular em 12x?". Agente que responde: "Sim! A taxa é de X% ao mês." Agente que ajuda a decidir: "Pode, mas as parcelas vão pesar na sua renda nos próximos meses, e seu aluguel vence dia 10. Quer ver uma alternativa?"

## O que a abertura disse (falas, em ordem de peso)

- **Confiança acima de tudo.** "Confiança é top of mind. Confiança é tudo." Uma recomendação errada faz o cliente não voltar. Portões de qualidade antes de escalar: satisfação, latência, alucinação. Sempre um humano à disposição.
- **Bem-estar, não aula.** Analogia do médico: o cliente quer bem-estar, não curso de finanças. Nada professoral.
- **Ajudar a decidir na hora.** "Na hora que ele tem a dúvida, na hora que ele precisa pagar uma conta." O ia.i tem três formas de conversa: nativa (receptiva), conectada (insight durante a navegação) e "do contexto à ação" (entra no ponto de dor ou fricção, por exemplo quando o cliente já está simulando um crédito).
- **Pouco push, muita individualização.** "Não quero o banco mandando push." "Não queremos experiências genéricas." Nível CPF, momento de vida, tom de voz.
- **Antecipar compromissos para não virar bola de neve.** "Quanto está comprometido." Só olhar o passado não resolve o futuro.
- **Hábito pouco a pouco, celebrando com o cliente.** "Não só agora, mas todos os dias, de pouquinho em pouquinho, e celebrando também isso com o cliente."
- **O caminho citado pelo líder do ia.i:** "Talvez ela tomar um crédito mais barato, cortar 70 reais aqui por mês nisso ou naquilo, ela consiga colocar as finanças em ordem. Mas não tem quem oriente ela."
- **A frase do pilar organizar o hoje:** "o quanto cabe no seu bolso".
- **Estratégia do banco:** crescer carteira com inadimplência em mínima histórica; "20 a 30% de maior acurácia nas concessões, menos default". O agente que reduz perda e mantém o cliente está alinhado à estratégia declarada.
- **ia.i hoje:** quatro capacidades no piloto (intimidade com a vida financeira, explicar produtos, dicas de gestão, transações como PIX). Responde e dá deep link; ponta a ponta no conversacional "em breve". 300 mil clientes, 3 milhões em setembro, toda a base em dezembro.

## O que a mentora pediu (14h10)

1. **Diferencial** em relação ao parcelamento de fatura que já existe no app e a simplesmente conversar com o ia.i. Não sobrepor: somar. Outra avaliadora repetiu a pergunta; o time não tinha resposta estruturada.
2. **Agêntico, não simulador conversacional.** Agir a partir de sinal do próprio cliente (ex.: simular o parcelamento várias vezes). Definir onde o estímulo aparece (dentro do ia.i, na tela do cartão).
3. **Determinístico x LLM.** O agente não pode imputar taxas. Separar regra, consulta à base, RAG e LLM. Pergunta de sim ou não é determinística. Monitorar 3 meses sem LLM, por custo.
4. **Consentimento explícito** para usar os dados e para o agente ser acionado por comportamento.
5. **Recorte.** Cartões parceiros (Marisa, Azul etc.) têm taxas negociadas fora do Itaú; correntista e não correntista se comportam diferente; parcelas já existentes e limite; preservar despesas essenciais.
6. **Rentabilidade.** O juro que sai de um lugar precisa entrar em outro (crédito pessoal, consignado, prazo maior, crédito com garantia de imóvel). A conta do banco precisa fechar.
7. **Público.** Se for só para inadimplente, o público é pequeno (o Itaú tem a menor inadimplência). Definir o público do piloto.
8. **Guardrails próprios** do case, sem depender só dos do ia.i. Segurança mínima (concorrência, DoS). Limitar escopo para o LLM não alucinar. Separar MVP de futuro (Open Finance, Itaú Shop, garantia de imóvel).
9. **Métricas do piloto:** taxa de recomendação aceita; saída do comportamento em cerca de 3 meses; ganho marginal x ganho total; relação com churn.
10. **Impacto social** conta, mesmo que não esteja escrito na rubrica.

Teste ao vivo: a mentora perguntou ao ia.i sobre fatura apertada; ele analisou os gastos do mês no cartão e não sugeriu plano algum.

## O que a staff definiu

- Entregáveis obrigatórios: proposta de negócio, protótipo funcional, documento de racional de prototipação, desenho da solução, documento explicativo da arquitetura com detalhamento dos componentes.
- Demo: algo navegável amanhã, pode ser mock, com QR code para a banca usar durante o pitch.
- Pitch: cerca de 3 minutos (a confirmar). Mostrar a arquitetura em uma pincelada, não em detalhe.
- Pivô é permitido com boa justificativa.
- Infraestrutura: projeto `batalha-time-05-xew3` (us-central1), BigQuery `hackathon_dados.extrato_sintetico`, Agent Platform, Cloud Run, Cloud Build, Artifact Registry (repo `agentes`), Secret Manager (`gemini-api-key`), Gemini CLI / AI Studio / Antigravity. Até US$ 1.000 em créditos por grupo; Antigravity e Gemini CLI consomem esse orçamento.

## DESCONHECIDO

Rubrica e pesos da avaliação; duração exata do pitch; composição da banca; se o ia.i tem plano de saída da fatura no roadmap; regras de elegibilidade dos produtos de crédito; custo de funding, perda em caso de calote e interchange do banco.
