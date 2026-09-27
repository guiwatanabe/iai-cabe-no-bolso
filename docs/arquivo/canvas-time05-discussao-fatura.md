# Time 05 — dor e canvas para discussão e submissão

Versão de 26/09/2026, baseada na transcrição disponível da reunião pausada
[Solution planning for well-being, iniciada às 11h32](https://notes.granola.ai/d/e76330ae-b201-4aed-a503-b4a6ac5f5e79)
e na conferência local da base sintética. Este é um recorte recomendado para
o time avaliar; a transcrição não registra uma decisão final da equipe.

## Frase da dor recomendada

> Quando a fatura não cabe no mês, o cliente não sabe como agir sem comprometer
> contas essenciais ou aumentar a dívida.

Na voz do cliente:

> Minha fatura não cabe neste mês. O que eu faço agora sem deixar faltar para
> uma conta importante e piorar o próximo mês?

Essa formulação reúne pessoa, momento, decisão difícil e consequência.
Mantém espaço para testar a solução. A dificuldade de decidir e o desconforto
do cliente são hipóteses apoiadas nos relatos da discussão, não fatos provados
pelo extrato. Os dados confirmam padrões de lançamentos que justificam testar
essa hipótese.

## O que a discussão acrescenta

- O time questiona repetidamente soluções amplas de planejamento financeiro
  e a semelhança com o IAI.
- No trecho final, surge um recorte mais específico: pressão de caixa,
  pagamento parcial/mínimo da fatura e risco de prolongar a dívida.
- Há interesse em antecipação, linguagem curta, uma ação possível e
  acompanhamento; gamificação foi aventada como mecanismo de engajamento.
- Não houve fechamento de nome, solução, arquitetura detalhada ou donos.
- Uma fala atribuída à organização informa **submissão até 13h de 26/09/2026**
  e permite pivô com justificativa. É a atualização mais recente encontrada,
  posterior ao cronograma da abertura.

Eu retiraria da formulação expressões como “gasta sem pensar” e “é
desorganizado”. A base não revela intenção, culpa, falta de conhecimento ou
por que alguém pagou parcialmente. Também não comprova o ciclo causal
“gastou → atrasou → pagou parcial → pagou mais juros”. Esse ciclo é hipótese
de investigação, não evidência já demonstrada.

## Texto dos sete campos

O arquivo oficial tem seis blocos de conteúdo, além do nome do agente e da
identificação da equipe. Abaixo estão os seis blocos mais o nome, como sete
campos. **Equipe: Time 05.**

### 1. Nome do agente

**Farol — jornada da fatura.** Nome de trabalho, derivado da ideia de “farol”
citada na reunião. O nome pode ser alterado sem mudar a dor.

### 2. O problema

Quando a fatura não cabe no mês, o cliente não sabe como agir sem comprometer
contas essenciais ou aumentar a dívida.

Dois sinais na base sintética de 2025 justificam investigar essa dor:

- **689 dos 1.000 perfis** têm pelo menos um lançamento de pagamento de
  fatura descrito como parcial ou mínimo.
- **327 dos 1.000 perfis** têm pelo menos um lançamento com `saldo_apos < 0`.

Esses grupos não devem ser somados nem tratados como o mesmo grupo. Os
registros não comprovam atraso, valor total devido ou razão do pagamento.

### 3. Momento do usuário

Cliente pessoa física com sinais de pressão no orçamento e histórico de
pagamento parcial/mínimo, nos dias anteriores ao vencimento confirmado da
fatura. Ele precisa escolher o que ajustar e como pagar, preservando despesas
essenciais. Valor total, vencimento e compromissos futuros devem vir de fonte
confirmada ou ser perguntados ao cliente; não constam de forma estruturada
no extrato fornecido.

### 4. Proposta do agente

**Ajudar o cliente a escolher e acompanhar um plano para a fatura antes do
vencimento, deixando claros os efeitos sobre este mês e o seguinte.**

Três capacidades:

1. Identificar sinais de pressão e confirmar as informações que faltam,
   incluindo fatura, entradas previstas e contas essenciais.
2. Comparar alternativas viáveis e explicar seus efeitos no caixa e na dívida,
   sem presumir taxas ou atribuir toda saída a consumo.
3. Registrar a ação escolhida, acompanhar sua conclusão e revisar o plano
   quando o contexto mudar.

O agente deve reconhecer quando o orçamento não permite quitar a fatura sem
sacrificar despesas essenciais. Nessa situação, explica os limites e ajuda
a comparar alternativas com condições confirmadas; não promete eliminar
juros nem oferece crédito com termos inventados.

### 5. Dados e tecnologia

Fluxo proposto em cinco etapas:

1. **Ler o histórico:** identificar pagamentos de fatura, entradas, saídas e
   sinais de pressão na base sintética.
2. **Confirmar o contexto:** obter valor/vencimento da fatura, saldo utilizável
   e despesas essenciais, perguntando o que estiver ausente.
3. **Calcular cenários:** funções determinísticas com regras explícitas,
   tratamento de transferências e cuidado com dupla contagem de cartão.
4. **Orientar a escolha:** agente em ADK/Gemini explica o cenário e as opções
   em linguagem curta, com origem dos números e incertezas.
5. **Registrar e acompanhar:** interface/API no Cloud Run registra a escolha
   e mostra o acompanhamento. Integração bancária é simulada no protótipo.

**Tecnologias propostas:** Google ADK/Gemini, BigQuery e Cloud Run. A análise
exploratória usa a cópia local já baixada. O desenho acima é uma proposta;
não existe implementação funcional dessas etapas nesta pasta.

### 6. Como mediremos valor

- **Ação:** proporção de planos aceitos e de ações confirmadas como concluídas
  antes do vencimento.
- **Compreensão:** proporção de clientes que identificam corretamente o
  efeito da alternativa escolhida, em uma pergunta curta.
- **Resultado no piloto:** recorrência de pagamentos parciais/mínimos entre
  os clientes atendidos, comparada ao histórico e a um grupo de comparação
  quando viável.

Na demo, mostrar cálculo, escolha e registro da ação. Não alegar redução
causal de dívida, juros, ansiedade ou inadimplência a partir do protótipo.
Metas numéricas e período do piloto ainda precisam ser definidos.

### 7. Escopo da demo

**Uma jornada:** um perfil sintético com histórico de pagamento parcial/mínimo
recebe um sinal de atenção antes de um vencimento simulado. Confirma o valor
da fatura e os compromissos essenciais; vê a diferença entre os recursos
confirmados e o necessário; compara alternativas; escolhe uma ação; acompanha
a atualização do plano.

O cenário precisa mostrar o efeito concreto de uma escolha: por exemplo,
adiar uma despesa opcional reduz a falta de caixa estimada; se isso não for
suficiente, o agente reconhece a lacuna e pede condições reais para comparar
uma negociação.

**Limites explícitos:** base sintética de 2025; fatura, vencimento e condições
adicionais da demo identificados como dados simulados/confirmados; nenhuma
contratação ou pagamento real; acompanhamento futuro demonstrado por replay
ou cenário, não por resultado real já obtido.

## Correções dos números da reunião

Conferência feita no CSV local completo, usando os rótulos tal como fornecidos.

| Tema | Formulação defensável |
| --- | --- |
| Tamanho da base | 467.585 lançamentos, 1.000 IDs, 12 meses de 2025. |
| “94% ficaram negativos” | 942 IDs tiveram algum mês com soma das entradas menor que soma das saídas registradas. Isso é fluxo líquido negativo. Apenas 327 IDs têm algum `saldo_apos < 0`. |
| “24–25% dos clientes pagaram parcial/mínimo” | São 689 IDs, ou 68,9%. São 3.177 dos 12.000 registros de pagamento de fatura, ou 26,475% desses registros. |
| “18% são juros” | Não. Pagamento de fatura equivale a 17,9532% do valor das saídas registradas. A microcategoria Juros pagos equivale a 0,5326%. |
| “Produtos financeiros” | 19,7052% do valor das saídas registradas; 91,1085% do valor dessa categoria está em Pagamento de fatura. Não chamar o total de juros ou custos evitáveis. |
| “26–27% são empréstimos” | Empréstimos e financiamentos somam 24,5656% do valor das saídas registradas. |

Regra para parcial/mínimo: filtrar `nom_cate_micro = 'Pagamento de fatura'`
e descrições terminadas em `parcial` ou `minimo`; contar IDs distintos na
união dos dois grupos. São 657 IDs com parcial e 529 com mínimo; 497 aparecem
nos dois. Logo, não somar os dois totais.

Não há coluna de valor total da fatura, vencimento, contrato ou saldo devedor.
Não é possível calcular o que faltou pagar, identificar atraso ou atribuir
juros àquela fatura apenas com esses dados. Há lançamentos de compras no
cartão e de pagamento de fatura, portanto os totais de saída também exigem
conciliação antes de representar consumo ou capacidade de pagamento.

## O que pode diferenciar — hipótese a testar

A hipótese de diferença é fazer o cliente sair da interação com uma ação
específica escolhida, seu efeito compreendido e acompanhamento do plano
até o vencimento. Um alerta, uma previsão ou três agentes especializados
isoladamente não demonstram essa diferença.

Não defender a proposta com a afirmação “o IAI não executa ações”: a plenária
citou execução de transações, e a discussão admite desconhecimento da
cobertura do beta. A comparação real ainda está pendente.

Para esta submissão, eu deixaria missões, pontos e recompensas como hipótese
posterior de engajamento. Primeiro testar se a orientação e a ação resolvem
a decisão da fatura. O mesmo vale para ampliar para investimentos, compras
parceladas e planejamento completo.

## Fala curta para alinhar o time

> Nossa hipótese de dor é a decisão da fatura que não cabe no orçamento.
> Queremos ajudar o cliente, antes do vencimento, a escolher o que ajustar
> sem comprometer contas essenciais e sem empurrar o problema para o mês
> seguinte. Vamos demonstrar uma jornada em que ele confirma os dados que
> faltam, entende as alternativas, escolhe uma ação e acompanha o plano.
> A base traz sinais para investigar essa dor; ainda precisamos testar se
> esse acompanhamento gera valor e em que ele difere do IAI.

## Fontes locais

- Transcrição: `/Users/manasses/Documents/ChatGPT/Batalha de Agentes/fontes/transcricao-discussao-time05-2026-09-26.txt`.
- Dados: `/Users/manasses/Documents/ChatGPT/Batalha de Agentes/data/extrato_sintetico.csv.gz`.
- Template: `/Users/manasses/Downloads/Batalha de Agentes - Ficha de Projeto Template.pptx`.

A transcrição é a versão disponível na pausa, sem timestamps por fala. As
letras dos locutores não foram associadas a identidades. Foram preservados
os erros de reconhecimento no arquivo bruto; o resumo acima usa a sequência
e o conteúdo da discussão, não a identidade presumida dos falantes.
