# Time 05 — texto do canvas

Versão de 26/09/2026. Nome e solução abaixo são propostas para avaliação do
time. O template contém seis blocos de conteúdo, além do nome e da equipe.

## 1. Nome do agente

**NEO — seu próximo passo para sair do aperto.**

Nome de trabalho na linha de Matrix, conforme direção de Maná.

**Equipe:** Time 05.

## 2. O problema

**Na voz do cliente: “Quanto mais apertado eu fico, mais caro fica sair do aperto.”**

O cliente precisa financiar a fatura para atravessar o mês, mas os encargos
consomem a renda seguinte, deixando cada vez menos dinheiro para pagar as
contas e sair do aperto.

Dois sinais que justificam investigar essa hipótese na base sintética:

- 689 dos 1.000 perfis têm ao menos um pagamento descrito como parcial ou mínimo.
- 327 dos 1.000 perfis têm ao menos um lançamento com saldo posterior negativo.

São grupos distintos. Esses registros não comprovam o motivo do pagamento,
contratação de financiamento ou a sequência causal de aumento dos encargos.

## 3. Momento do usuário

Cliente pessoa física com orçamento pressionado e histórico de pagamento
parcial/mínimo, diante da próxima fatura. Antes do vencimento confirmado,
precisa decidir como pagar sem comprometer despesas essenciais nem assumir
um compromisso inviável no mês seguinte.

## 4. Proposta do agente

**Ajudar o cliente a recuperar margem financeira com um plano executável para
a próxima fatura, preservando despesas essenciais e reduzindo a necessidade
de repetir o financiamento.**

Três capacidades:

1. **Dimensionar o aperto:** confirmar fatura, vencimento, recursos disponíveis
   e contas essenciais; calcular quanto falta e em qual data.
2. **Comparar saídas viáveis:** simular ajustes aceitos pelo cliente e,
   quando necessário, propostas oficiais de negociação, mostrando custo
   total e efeito no caixa deste mês e dos seguintes.
3. **Acompanhar a escolha:** registrar a ação e seu prazo, apoiar a execução
   autorizada e revisar o plano quando o contexto mudar.

Se não houver uma saída viável com os dados e condições disponíveis, o agente
explicita a insuficiência e encaminha para atendimento/negociação. Não
presume que todo aperto possa ser resolvido cortando gastos.

## 5. Dados e tecnologia

**Fluxo:** extrato → confirmação do contexto → cálculo de cenários → escolha
orientada → acompanhamento.

- **Histórico:** base sintética de 2025 no BigQuery; cópia local para análise.
- **Dados adicionais:** valor total e vencimento da fatura, recursos
  utilizáveis, despesas essenciais e condições das alternativas, provenientes
  do cliente ou de fonte bancária confirmada.
- **Cálculo:** funções determinísticas para caixa e custo, com regras de
  arredondamento, prevenção de dupla contagem e pressupostos explícitos.
- **Agente:** Google ADK/Gemini para confirmar informações, acionar as
  ferramentas e explicar consequências.
- **Aplicação:** Cloud Run para interface/API e registro do plano.

Arquitetura proposta, ainda não implementada. Valores ausentes não são
inventados. Integrações bancárias serão simuladas na demo.

## 6. Como mediremos valor

1. **Valor a financiar e custo total projetado:** comparação entre o plano
   escolhido e o cenário sem ajuste, no mesmo horizonte e com condições
   confirmadas. Na demo, são projeções, não economia realizada.
2. **Execução:** proporção de ações escolhidas e confirmadas como concluídas
   antes do vencimento.
3. **Compreensão:** proporção de clientes que conseguem identificar o efeito
   da alternativa escolhida em uma pergunta curta.
4. **Resultado no piloto:** recorrência de pagamentos parciais/mínimos nos
   ciclos seguintes, com comparação ao histórico e grupo de comparação
   quando viável.

Metas e período do piloto ainda serão definidos. Não alegar redução de
inadimplência, juros ou ansiedade com base apenas na demonstração.

## 7. Escopo da demo

**Uma pessoa, uma fatura, uma decisão acompanhada.**

Um cliente sintético com sinais de pressão confirma a fatura e as contas
essenciais. O agente calcula a falta de caixa, apresenta duas alternativas
viáveis e mostra como cada uma afeta os próximos compromissos. O cliente
escolhe uma ação; o plano é recalculado e o acompanhamento fica registrado.

A tela deve mostrar o valor a financiar, o custo total quando houver
condições confirmadas e quanto sobra para as despesas essenciais. Uma etapa
de replay pode mostrar a revisão do plano após uma mudança de contexto.

**Limites:** dados sintéticos; valor/vencimento da fatura e condições
complementares identificados como simulados ou confirmados; nenhuma
contratação ou pagamento real; nenhum resultado futuro apresentado como
observado.

## Fala de alinhamento para o time

> Nossa dor é: quanto mais apertado eu fico, mais caro fica sair do aperto.
> Vamos atuar no momento da fatura, ajudando o cliente a escolher uma saída
> viável que preserve o essencial e não agrave o próximo mês. A demo vai
> mostrar uma decisão completa: entender quanto falta, comparar alternativas,
> escolher uma ação e acompanhar o plano.

## Base das informações

- [Discussão pausada do time, Granola, 26/09/2026 às 11h32](https://notes.granola.ai/d/e76330ae-b201-4aed-a503-b4a6ac5f5e79).
- Base local: `/Users/manasses/Documents/ChatGPT/Batalha de Agentes/data/extrato_sintetico.csv.gz`.
- Análise e correções dos números: `/Users/manasses/Documents/ChatGPT/Batalha de Agentes/output/canvas-time05-discussao-fatura.md`.

As contagens foram conferidas sobre os 467.585 registros de 2025. “Parcial”
e “mínimo” são rótulos das descrições na microcategoria Pagamento de fatura.
Não há total/vencimento/chave de fatura para provar o valor não pago ou atraso.
Não há sequência intradiária; transferências e compras/faturas exigem
conciliação. O ciclo de aperto e encargos é a hipótese de dor desta proposta.
