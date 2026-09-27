# Revisão de IA responsável

Revisão de 27/09 do desenho do serviço (prompt do agente, validador, guardrails, ferramentas MCP e camada analítica) contra os princípios de governança de dados. Só leitura: nada foi alterado. O objetivo é alinhar o time sobre o que já está coberto e o que falta antes de produção.

## Resumo

Os prompts são, em geral, neutros, e as proteções estão acima da média: checagens em código, validador com regras R1–R20, números sempre vindos das ferramentas e transferência para uma pessoa em casos de risco. Sobram dois problemas:

1. **Alguns exemplos do prompt contradizem as próprias regras.** Há promessas, julgamentos sem dado e enquadramento de venda. O modelo tende a copiar os exemplos mais do que as regras.
2. **O texto afirma com mais certeza do que os dados permitem.** "Cabe no seu mês" e "saldo previsto na conta" se apoiam em números que a própria documentação diz estarem enviesados.

| Princípio | Situação |
|---|---|
| Centralidade nos clientes e indivíduos | Parcial |
| Privacidade e proteção de dados | Bom, com lacunas |
| Transparência e comunicação aberta | Parcial |
| Justiça e não discriminação | Requer atenção |
| Qualidade dos dados | Maior risco |
| Responsabilidade | Boa estrutura, uma lacuna |
| Ética algorítmica | Regras boas, exemplos a corrigir |

## 1. Centralidade nos clientes e indivíduos

**O que está bom**
- A troca de vencimento aparece antes de qualquer crédito, porque resolve sem dívida nova.
- As ofertas vêm do menor para o maior custo total.
- Uma recusa é respeitada, sem nova oferta na mesma conversa (R7).
- A escolha de pagar o mínimo é respeitada, com os custos informados uma vez.

**Lacuna**
- No gatilho `pagar_outro_valor` ([instruction.md, linha 15](../agents/cabe/instruction.md#L15)), o agente só fala quando há crédito para oferecer. Sem oferta, fica em silêncio. O cliente prestes a pagar abaixo do total não fica sabendo quanto o mínimo vai custar. O agente entra na conversa para vender, e não porque o cliente precisa de ajuda.

## 2. Privacidade e proteção de dados

**O que está bom**
- Sem consentimento, o código limita o que chega ao modelo, e `explicar_fatura` é bloqueada.
- `cliente_id` e o estado da sessão são injetados pelo código, nunca pelo modelo.
- O modelo não vê o grupo do cliente nem a quantidade de pagamentos abaixo do total.
- O validador não registra texto do cliente nos logs.

**Lacunas**
- O `BigQueryAgentAnalyticsPlugin` ([agent.py, linha 61](../agents/cabe/agent.py#L61)) grava as conversas completas no dataset `agent_logs`. Isso inclui falas sensíveis como "estou devendo em todo lugar". Não há prazo de retenção, finalidade ou regra de minimização documentados.
- Ao pedir consentimento (exemplo C8, "É só ativar a análise"), o agente não diz quais dados são usados (90 dias de movimentação da conta) nem para quê. Isso não atende ao consentimento informado da LGPD.
- `publico_vulneravel` (recebeu benefício do INSS na janela) funciona como indicador indireto de idade ou deficiência. Ele vai ao modelo em toda chamada, embora só importe quando uma oferta com consignado é aceita.

## 3. Transparência e comunicação aberta

**O que está bom**
- Previsões são apresentadas como previsão ("pela previsão"), nunca como certeza.
- O agente diz que é o ia.i e nunca se apresenta como pessoa.
- Todo número tem origem rastreável (`numeros_citados` e `meta.origem`).

**Lacunas**
- A taxa do cheque especial é "ilustrativa", e a do consignado está "a conferir" ([taxas.yaml](../mcp_server/taxas.yaml)). O campo `contexto.simulacao` existe, mas o prompt não manda o modelo avisar o cliente. Custos ilustrativos aparecem como se fossem reais.
- Quando não há oferta, o cliente não recebe explicação nem tem como contestar. O art. 20 da LGPD garante revisão de decisões automatizadas de crédito e informação sobre os critérios usados. Hoje o único caminho é "falar com alguém da equipe".

## 4. Justiça e não discriminação

**Lacunas**
- **Renda irregular exclui de todas as ofertas.** As ofertas exigem `confianca_recebimento == "ALTA"`, ou seja, entradas nos 3 meses ([ofertas.py, linha 36](../mcp_server/core/ofertas.py#L36)). Autônomos e trabalhadores informais ficam sem o crédito mais barato e seguem no rotativo, a 14% ao mês. A regra é defensável como proteção, mas o custo dela para esse público precisa estar documentado e aceito.
- **O 13º salário enviesa a segmentação.** Entre clientes com recebimento de folha, 311 de 311 do grupo SEMPRE_QUITA recebem 13º, contra 68 de 204 do NO_LIMITE. O gradiente entre grupos mede, em parte, emprego formal ([mudancas.md](../sql/docs/mudancas.md), aviso de 27/09).
- **A proteção ao público vulnerável é estreita.** Ela só cobre o consignado; a cobertura com cheque especial não passa por uma pessoa.
- **Não há avaliação de justiça.** O conjunto de avaliação não tem o caso C10 (público vulnerável) nem comparação de resultados entre grupos.

## 5. Qualidade dos dados

É o maior risco, porque os números vão direto para o texto que o cliente lê e para a decisão de oferta.

- **"Cabe no seu mês" se apoia em folga superestimada.** A regra 3b e o exemplo I4 dizem que as parcelas "cabem no orçamento". O teto da parcela é `folga_mensal`, que a documentação declara superestimada (ignora gasto variável e está inflada pelo 13º).
- **"Saldo previsto na conta" não é saldo em conta.** O rótulo em [server.py, linha 73](../mcp_server/server.py#L73) diz "saldo previsto na conta", mas o valor é o fluxo líquido do ciclo, começando do zero. O próprio `mudancas.md` diz que ele não pode ser apresentado como saldo.
- **Outras distorções conhecidas:**
  - O primeiro ciclo é parcial e puxa o tipo de falta para "recorrente".
  - O valor típico de recebimento é a mediana de todas as entradas, não da folha, e bloqueia clientes elegíveis da cobertura curta.
  - A fatura é estimada como 1,33 × as compras do mês anterior.
- **O SQL novo ainda não rodou no BigQuery.** Os números de homologação são da versão antiga ou de emulação local.

## 6. Responsabilidade

**O que está bom**
- Checagens em código primeiro, validador depois.
- Casos de risco vão direto para uma pessoa (R8).
- Toda reprovação é registrada com a regra.
- Falhas caem numa mensagem segura, com texto fixo.

**Lacunas**
- `DIRETRIZES_ITAU` ainda é um texto provisório ("texto oficial pendente", [validador.py, linha 20](../agents/cabe/validador.py#L20)). A R13 é bloqueante, mas é checada contra nada.
- O filtro de manipulação de instruções em código só reconhece frases em inglês ([guardrails.py, linha 48](../agents/cabe/guardrails.py#L48)). Tentativas em português dependem só do modelo.

## 7. Ética algorítmica e neutralidade dos prompts

**O que está bom**
- Há regras explícitas contra julgar, culpar, comparar, ameaçar e prometer.
- Termos internos ("pedalada", "Escorregão", "Rolando a fatura") são proibidos na saída e checados em código.

**Lacunas nos exemplos** ([instruction.md](../agents/cabe/instruction.md))

| Exemplo | Trecho | Problema |
|---|---|---|
| C1 (linha 167) | "o cheque especial é coberto na hora, e eu te aviso quando zerar" | Promessa, proibida pela R6 |
| C2 (linha 172) | "estão ocupando um bom espaço do seu orçamento" | Julgamento sem fato que o sustente |
| C2 (linha 172) | "parcelas de faturas anteriores" | Rótulo errado: o fato é de parcelas de compras em andamento |
| C2 (linha 172) | "Assim seu limite do cartão volta inteiro" | Mostra o benefício e só dá o custo total se o cliente pedir |
| I2 (linha 150) | "Temos um jeito de pagar tudo" | Empurra para o crédito |
| I4 (linha 157) | "parcelas que cabem no seu mês" | Afirma que cabe (ver item 5) e não dá custo nenhum |
| Regra 3b (linha 53) | "a cobertura com cheque especial tende a ser a mais adequada" | O agente recomenda um produto |
| C9 (linha 205) | "Ótimo." | Entusiasmo quando um cliente vulnerável aceita consignado |

## Prioridades antes de produção

1. **Alinhar os exemplos às regras:** sem promessas, sem julgamento sem dado, e custo total na mesma mensagem que o benefício.
2. **Parar de afirmar que cabe:** tirar "cabe no seu mês" e "saldo na conta" até folga e saldo previsto serem corrigidos.
3. **Colocar as diretrizes oficiais do Itaú no validador.**
4. **Avisar e explicar:**
   - dizer ao cliente que as taxas são ilustrativas;
   - explicar o que o consentimento cobre;
   - dar um caminho para entender ou contestar a falta de oferta.
5. **Documentar e avaliar:**
   - documentar retenção e finalidade de `agent_logs`;
   - adicionar casos de avaliação de público vulnerável e de comparação entre grupos.

## Pontos para decisão do time

- Manter a exclusão por renda irregular, sabendo que deixa esse público no rotativo?
- Ampliar a revisão humana do público vulnerável para a cobertura com cheque especial?
- No gatilho `pagar_outro_valor` sem oferta, informar o custo do mínimo em vez de ficar em silêncio?
- Qual o prazo de retenção das conversas em `agent_logs`, e quem tem acesso?
