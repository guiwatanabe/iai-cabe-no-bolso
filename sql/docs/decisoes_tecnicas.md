# Cabe no Bolso

## Arquitetura de Dados, Tabelas e Decisões Técnicas do MVP

**Objetivo deste documento:** explicar, em linguagem simples, como os
dados saem do extrato bruto, viram sinais financeiros confiáveis e
chegam ao agente de IA sem deixar o LLM fazer contas ou decidir crédito.

### Visão em uma frase

A camada analítica responde: **"essa fatura cabe no orçamento, quanto
falta e o problema parece pontual ou recorrente?"**

Depois, o agente usa esse contexto para conversar e apresentar apenas
caminhos permitidos pelas regras do produto.

------------------------------------------------------------------------

## 1. O problema que estamos resolvendo

O Cabe no Bolso atua quando existe risco de a fatura do cartão não caber
no caixa do cliente. A proposta não é criar um planejador financeiro
genérico. O foco é a jornada de **fatura e pagamento**.

-   Projetar a capacidade de pagamento antes do vencimento.
-   Distinguir uma falta pontual de uma falta recorrente.
-   Evitar oferecer crédito quando a fatura já cabe.
-   Não deixar a IA aprovar crédito nem fazer cálculos financeiros
    livremente.
-   Entregar ao agente apenas um contexto estruturado e auditável.

> **Regra de arquitetura:** SQL/Python calculam. O LLM conversa. Isso
> reduz erro numérico, melhora a explicabilidade e deixa as regras de
> negócio auditáveis.

### Público do MVP

  -----------------------------------------------------------------------
  Grupo                   Definição histórica     Papel no MVP
  ----------------------- ----------------------- -----------------------
  `SEMPRE_QUITA`          Não apresenta o padrão  Referência / sem oferta
                          de pagamento abaixo do  automática.
                          total.                  

  `ESCORREGAO`            1-2 episódios no ano.   MVP: investigar falta
                                                  pontual e cobertura
                                                  curta.

  `ROLANDO_FATURA`        3-5 episódios no ano.   MVP: investigar falta
                                                  recorrente e
                                                  parcelamento.

  `NO_LIMITE`             6+ episódios no ano.    Fora do MVP inicial;
                                                  exige solução mais
                                                  especializada.
  -----------------------------------------------------------------------

A segmentação anual é contexto histórico. Ela **não precisa ser igual**
ao diagnóstico dos últimos 90 dias. Por exemplo, um cliente classificado
como Escorregão no ano pode estar vivendo dois ciclos ruins recentes.

------------------------------------------------------------------------

## 2. Arquitetura geral

A estrutura foi organizada em quatro blocos simples:

  -----------------------------------------------------------------------
  Camada                  Função                  Exemplo
  ----------------------- ----------------------- -----------------------
  Bronze                  Dados recebidos quase   Extrato anual e janela
                          como vieram da origem.  D-90.

  Silver                  Organiza e calcula      Recebimentos,
                          fatos financeiros       compromissos, cartão e
                          reutilizáveis.          ciclos.

  Gold                    Transforma os fatos em  Capacidade,
                          contexto de negócio.    elegibilidade e
                                                  contexto do agente.

  Tools / Agente          Aplica regras de        Ofertas, explicação da
                          produto e conversa com  fatura e ia.i.
                          o cliente.              
  -----------------------------------------------------------------------

**Fluxo:**
`Bronze → Silver → Gold → Tools → Agente Cabe no Bolso / ia.i`

### Por que separar assim?

-   Se uma regra financeira mudar, alteramos o motor sem reescrever o
    agente.
-   Podemos testar cada tabela isoladamente.
-   O agente recebe poucos campos, com significado claro.
-   A aprovação de crédito continua fora da IA.

### Janelas de dados

-   **12 meses:** usados para a persona histórica do cliente.
-   **D-90:** usados para entender a situação financeira recente e
    projetar capacidade.
-   **D-5:** no protótipo, simula o momento em que ainda existem alguns
    dias para agir antes do vencimento.

------------------------------------------------------------------------

## 3. Bronze: ponto de partida

  -----------------------------------------------------------------------
  Tabela                  O que contém            Uso
  ----------------------- ----------------------- -----------------------
  `extrato_sintetico`     Histórico de 2025.      Segmentação anual e
                                                  histórico de pagamento
                                                  de fatura.

  `cash90_hackathon`      Janela recente de       Motor de capacidade e
                          aproximadamente 90      resumo de transações.
                          dias.                   
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 4. Silver: fatos financeiros

  -------------------------------------------------------------------------------
  \#                      Tabela                          Responsabilidade
                                                          principal
  ----------------------- ------------------------------- -----------------------
  01                      `silver_cliente_dia`            Organiza a movimentação
                                                          do cliente por dia.

  02                      `silver_recebimentos`           Estima renda,
                                                          recebimento típico, dia
                                                          esperado e confiança.

  03                      `silver_historico_fatura_12m`   Conta o comportamento
                                                          anual de pagamento e
                                                          define a persona.

  04                      `silver_compromissos`           Resume despesas
                                                          recorrentes e parcelas
                                                          em curso.

  05                      `silver_cartao`                 Resume compras do
                                                          cartão e estima a
                                                          fatura do protótipo.

  06                      `silver_cliente_features`       Junta as principais
                                                          features do cliente.

  07                      `silver_ciclo_fatura`           Projeta capacidade por
                                                          ciclo e identifica
                                                          falta
                                                          pontual/recorrente.

  08                      `silver_transacoes_resumo`      Resume compras por
                                                          mês/categoria para
                                                          explicar a fatura.
  -------------------------------------------------------------------------------

------------------------------------------------------------------------

## 5. Principais decisões técnicas da Silver

### 5.1 Recebimentos: 13º salário não é renda recorrente

O 13º aparecia fortemente na janela D-90 e fazia alguns clientes
parecerem mais folgados do que realmente são. Por isso, ele foi retirado
do cálculo de renda recorrente e do recebimento típico.

O dia esperado de recebimento prioriza salário CLT ou benefício INSS.
Quando isso não existe, usamos as demais entradas como fallback. Também
guardamos uma medida de confiança, pois previsão de recebimento não deve
ser tratada como certeza.

### 5.2 Fatura estimada no protótipo

A base do hackathon não traz o valor real da fatura. A aproximação
adotada foi:

**fatura estimada = 1,33 × compras do cartão do mês anterior**

O fator veio da relação observada entre compras do mês anterior e
pagamentos de fatura.

> Em produção, essa estimativa deve desaparecer: o motor deve consumir o
> valor real da fatura, data de fechamento e vencimento.

### 5.3 Folga mensal é uma feature auxiliar

A folga é calculada aproximadamente como renda menos compromissos
recorrentes. Ela ajuda em regras de produto, mas **não é usada sozinha
para decidir se a fatura cabe**, porque a base não representa
perfeitamente todos os gastos variáveis.

### 5.4 Parcelas

As parcelas em curso são mantidas como compromisso. Na tabela de
transações resumidas, compras parceladas são agrupadas na categoria
**Parcelas**, facilitando a explicação da composição da fatura.

------------------------------------------------------------------------

## 6. A tabela mais importante: `silver_ciclo_fatura`

Essa tabela responde à pergunta central do MVP: **no ciclo atual, existe
capacidade para cobrir a fatura?**

### Decisões que evitam falsos sinais

-   O 13º salário não entra como entrada recorrente.
-   Não existe carry-over entre ciclos, pois não temos um saldo inicial
    real confiável.
-   O ciclo 1 do D-90 é parcial e não é usado para afirmar recorrência.
-   Pagamento de fatura é excluído das saídas do fluxo para não contar a
    própria fatura duas vezes.
-   Compras no cartão também são excluídas do fluxo de caixa, pois já
    serão representadas pela fatura.
-   O ciclo atual é observado até D-5 e os últimos cinco dias são
    projetados com base nas caudas recentes.

### Por que abandonamos "saldo previsto"?

Sem um saldo inicial confiável, acumular entradas e saídas entre ciclos
criava dinheiro artificial.

Por isso, o conceito foi renomeado para **`caixa_disponivel_estimado`**:
ele representa a capacidade líquida gerada dentro daquele ciclo, não o
saldo real da conta.

### Três conceitos de déficit

  -------------------------------------------------------------------------
  Campo                     Significado simples     Pode ir para o cliente?
  ------------------------- ----------------------- -----------------------
  `deficit_pre_fatura`      O fluxo já estava       Normalmente não como
                            negativo antes de       "falta da fatura".
                            considerar a fatura.    

  `valor_faltante_fatura`   Parte da própria fatura Sim. É o campo correto
                            que não possui          para "faltam R\$ X".
                            cobertura.              

  `deficit_total_ciclo`     Pressão total: déficit  Uso do motor; exige
                            anterior + fatura.      contexto para
                                                    comunicar.
  -------------------------------------------------------------------------

**Invariante importante:**

`0 ≤ valor_faltante_fatura ≤ fatura_estimada`

Assim o agente nunca diz que faltam R\$ 8 mil para pagar uma fatura de
R\$ 1,5 mil.

------------------------------------------------------------------------

## 7. Como classificamos a falta

  Situação recente                           `tipo_falta`
  ------------------------------------------ --------------
  Ciclo atual não apresenta falta.           `SEM_FALTA`
  Somente o ciclo atual apresenta falta.     `PONTUAL`
  Ciclos completos 2 e 3 apresentam falta.   `RECORRENTE`

O ciclo 1 é usado como apoio para projeção, mas não para declarar
recorrência porque ele começa no meio da janela D-90.

### Resultado observado após homologação

  Grupo                Fatura não cabe   Pontual   Recorrente
  ------------------ ----------------- --------- ------------
  `SEMPRE_QUITA`                 15,4%     10,0%         5,5%
  `ESCORREGAO`                   63,4%     15,8%        47,5%
  `ROLANDO_FATURA`               57,6%     14,3%        43,2%
  `NO_LIMITE`                    41,7%     21,1%        20,6%

A leitura correta não é "Escorregão deveria sempre ter menos recorrência
que Rolando". A persona usa 12 meses; o tipo de falta usa os ciclos
recentes. São dimensões diferentes.

------------------------------------------------------------------------

## 8. Gold: transformando cálculo em decisão de produto

### Gold 01 - `gold_capacidade_pagamento`

Junta as features do cliente com o ciclo atual. Define se a fatura cabe
usando **`valor_faltante_fatura`**, além de calcular quantos dias faltam
até o próximo recebimento esperado.

### Gold 02 - `gold_elegibilidade`

Cria flags analíticas para os caminhos do MVP. Isso **não é aprovação de
crédito**.

  -----------------------------------------------------------------------
  Caminho                             Regras analíticas principais
  ----------------------------------- -----------------------------------
  Cobertura curta                     Escorregão + falta pontual + falta
                                      \> 0 + próximo recebimento entre 1
                                      e 25 dias + recebimento típico
                                      cobre a falta.

  Parcelamento                        Rolando a Fatura + falta
                                      recorrente + falta \> 0 + folga
                                      mensal positiva.

  Fora do MVP                         No Limite.

  Sem intervenção                     Fatura cabe ou não existe caminho
                                      automático seguro.
  -----------------------------------------------------------------------

### Distribuição homologada

  Grupo                Candidatos a cobertura curta   Candidatos a parcelamento
  ------------------ ------------------------------ ---------------------------
  `SEMPRE_QUITA`                                  0                           0
  `ESCORREGAO`                                   15                           0
  `ROLANDO_FATURA`                                0                          74
  `NO_LIMITE`                                     0                           0

O resultado é propositalmente conservador: o objetivo não é maximizar
venda de crédito, mas identificar situações em que um caminho pode fazer
sentido.

------------------------------------------------------------------------

## 9. Gold 03: contrato com o agente

A **`gold_contexto_agente`** é a fronteira entre o motor analítico e as
tools do agente.

Ela possui uma linha por cliente e valores monetários em centavos
(`INT64`), reduzindo ambiguidades de moeda e ponto flutuante.

### O que o agente recebe

  -----------------------------------------------------------------------
  Bloco                               Exemplos
  ----------------------------------- -----------------------------------
  Contexto                            Data simulada, grupo histórico,
                                      quantidade de pedaladas.

  Fatura                              Dia de vencimento e valor estimado.

  Capacidade                          Caixa disponível estimado e folga
                                      mensal.

  Recebimento                         Valor típico, dia esperado, dias
                                      até recebimento e confiança.

  Diagnóstico                         Valor faltante da fatura e tipo de
                                      falta.

  Compromissos                        Parcelas em curso.

  Guardrails                          Público vulnerável e flags de
                                      elegibilidade.
  -----------------------------------------------------------------------

> O agente recebe **`valor_faltante_fatura`**, não um déficit total
> apresentado como se fosse falta da fatura. Essa separação protege a
> clareza da conversa.

### O que o agente NÃO deve fazer

-   Recalcular a matemática financeira com o LLM.
-   Aprovar crédito.
-   Inventar limite, taxa, CET ou produto disponível.
-   Transformar a persona histórica em julgamento sobre o cliente.
-   Expor identificadores desnecessários como conta ou cartão.

------------------------------------------------------------------------

## 10. O que é MVP e o que muda em produção

  -----------------------------------------------------------------------
  Tema                    MVP Hackathon           Produção
  ----------------------- ----------------------- -----------------------
  Fatura                  Estimativa 1,33 ×       Valor real da fatura.
                          compras do mês          
                          anterior.               

  Vencimento              Inferido a partir do    Data real do cartão.
                          comportamento           
                          disponível.             

  Recebimentos            Estimados pelo          Entradas e agenda em
                          histórico D-90.         tempo real.

  Crédito                 Liberação               Serviço real de crédito
                          simulada/parâmetro.     e política Itaú.

  Contratação             Demonstração de         Fluxos oficiais após
                          jornada.                confirmação explícita.

  Público                 Escorregão e Rolando.   Expansão futura; No
                                                  Limite exige desenho
                                                  específico.

  Integração              Protótipo e tools.      Conta, cartões,
                                                  crédito, contratação e
                                                  atendimento humano.
  -----------------------------------------------------------------------

### Cenários priorizados no MVP

-   **Escorregão:** falta pontual e possível cobertura curta.
-   **Rolando:** falta recorrente e possível parcelamento.
-   **Cliente prefere pagar mínimo:** explicar custo uma vez e respeitar
    a decisão.
-   **Parcela não cabe:** não oferecer automaticamente e direcionar para
    alternativa humana.

### Consentimento

A análise personalizada depende de consentimento para usar os últimos 90
dias de conta e cartão.

Sem consentimento, a experiência deve ficar restrita às formas normais
de pagamento da fatura.

------------------------------------------------------------------------

## 11. Decisões técnicas que devemos defender na apresentação

  -----------------------------------------------------------------------
  Decisão                             Por quê
  ----------------------------------- -----------------------------------
  12 meses para persona; D-90 para    Separa comportamento histórico de
  capacidade                          situação recente.

  Excluir 13º                         Evita tratar renda extraordinária
                                      como capacidade recorrente.

  Sem carry-over                      Não existe saldo inicial confiável
                                      para reconstruir patrimônio entre
                                      ciclos.

  Ciclo 1 fora da recorrência         É um ciclo parcial causado pelo
                                      corte da janela.

  Caixa estimado ≠ saldo bancário     Evita prometer precisão que a base
                                      não possui.

  Separar falta da fatura e déficit   Mantém a comunicação
  total                               financeiramente coerente.

  SQL/Python fazem contas             O LLM não deve ser fonte da verdade
                                      numérica.

  Crédito fora da IA                  Elegibilidade analítica não é
                                      aprovação de crédito.

  Valores em centavos no contrato     Padroniza integração e evita
                                      ambiguidades monetárias.

  No Limite fora do MVP               O comportamento exige solução mais
                                      especializada e renda pode ser
                                      menos previsível.
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 12. Modelo mental final

Para explicar a solução de forma simples, pense em cinco perguntas:

  -----------------------------------------------------------------------
  Pergunta                            Quem responde?
  ----------------------------------- -----------------------------------
  1\. Quem é esse cliente             Histórico 12m / segmentação.
  historicamente?                     

  2\. Quanto normalmente entra e      Silver recebimentos.
  quando?                             

  3\. Quais compromissos e fatura     Silver compromissos + cartão.
  existem?                            

  4\. A fatura cabe neste ciclo? A    Silver ciclo + Gold capacidade.
  falta é pontual ou recorrente?      

  5\. Existe algum caminho permitido  Gold elegibilidade + serviço de
  para apresentar?                    crédito + tools.
  -----------------------------------------------------------------------

> **Resumo final:** o Cabe no Bolso não tenta adivinhar a vida
> financeira do cliente. Ele usa uma janela recente, regras
> determinísticas e contexto histórico para identificar um risco
> específico de fatura. A IA entra depois, para transformar esse
> diagnóstico em uma conversa simples, transparente e acionável.

### Status da camada analítica

  -----------------------------------------------------------------------
  Camada                              Status
  ----------------------------------- -----------------------------------
  Bronze                              Base disponível para o protótipo.

  Silver 01-08                        Homologada.

  Gold 01-03                          Homologada.

  Próximo passo                       Atualizar contrato Python
                                      (`Contexto`), regras de
                                      ofertas/tools e integração com o
                                      agente.
  -----------------------------------------------------------------------
