# 02 · Proposta de produto: Cabe no Bolso

Status: proposta em discussão pelo Time 05 (26/09/2026, 17h). A ficha enviada às 12h59 (`docs/arquivo/canvas-time05-submissao.md`) é compatível com esta proposta; ela promete "recuperar margem com um plano viável para a próxima fatura, preservando o essencial". Divergências abertas estão em `docs/decisoes.md`.

## Tese

O rotativo do cartão não é receita, é provisão: 65% de inadimplência em julho de 2026 segundo o Banco Central, e "um produto que tem 65% de inadimplência tem problema de design" (Galípolo). O Cabe no Bolso troca juro que o banco não recebe por parcela que o cliente paga, e fica com o cliente até ele sair. Cliente e banco ficam do mesmo lado.

**Em uma frase:** o banco oferece o mesmo parcelamento para todo mundo; o Cabe no Bolso descobre por que aquele cliente rola a fatura, monta a saída para essa causa com data para acabar e volta a cada fatura até ele pagar três inteiras seguidas.

## A dor

"Quanto mais apertado eu fico, mais caro fica sair do aperto." Na base: 689 de 1.000 clientes pagaram menos que o total da fatura em 2025; de 4 a 7 meses por ano, conforme o perfil; R$ 447 mil de juros de rotativo. Quatro causas para o mesmo sintoma (`01-evidencias-base.md`).

## Para quem

Correntistas do Itaú com cartão do próprio banco (cartões parceiros ficam fora do MVP). Piloto: grupo B (384 clientes, 3 a 5 faturas roladas no ano). Grupo A entra com a saída mais simples; grupo C entra sem crédito automático, com renegociação e humano.

## A jornada, com o valor de cada lado

| Etapa | O que o agente faz | Valor para o cliente | Valor para o banco |
|---|---|---|---|
| 1. Sinal e consentimento | Percebe o sinal (fatura acima de X% da renda com escolha de pagar menos que o total; ou 3ª fatura rolada; ou simulações repetidas de parcelamento) e pede autorização para usar o histórico | Ninguém empurra nada; ele escolhe | Consentimento explícito; aciona só quem tem o problema |
| 2. Diagnóstico | Lê o extrato e identifica a causa: renda comprometida, gasto do dia a dia (ou atípico), renda irregular, nenhuma clara | Entende por que está apertado, sem aula | Individualização; separa quem precisa de crédito de quem precisa de outra coisa |
| 3. Urgência | Quantas faturas rolou no ano (A/B/C) | Sabe o tamanho do problema | Triagem barata e determinística |
| 4. Plano com data para acabar | A: paga inteira com o limite da conta, só se cobrir até o próximo salário. B: paga o que cabe agora; o resto vira parcela fixa que cabe no mês (consignado se elegível, senão crédito pessoal, senão parcelamento da fatura). C: renegociação com atendimento humano | Sai de 14% ao mês; parcela previsível; recupera o limite do cartão | O saldo sai de uma linha com 65% de inadimplência para uma que é paga |
| 5. Teto do mês | Quanto ainda cabe no cartão até a próxima fatura, preservando o essencial | Não volta a estourar | O cartão continua girando dentro da capacidade; menos PDD |
| 6. Acompanhamento | Volta a cada fatura, ajusta, encerra após 3 inteiras seguidas, comemora | Vê o progresso | B não vira C; retenção; o cliente vira o perfil saudável da base |

O crédito aparece na etapa 4 como ferramenta. O que se vende é a etapa 6.

## A conta do banco

`receita esperada = saldo × taxa × (1 − inadimplência) − saldo × inadimplência × perda em caso de calote`

| Variável | Valor usado | Fonte | Status |
|---|---|---|---|
| Taxa do rotativo | 14% a.m. na base; 424,5% a.a. (jan/26) e 436,2% a.a. (jul/26) no mercado | Base; BC | Confirmada |
| Parcelamento de fatura | 194,9% a.a. (~9,4% a.m.) | BC jan/26 | Confirmada |
| Crédito pessoal não consignado | Parâmetro; ordem de 6% a.m. | Série BC | Aproximada, conferir |
| Consignado (INSS; CLT via Crédito do Trabalhador) | Parâmetro; INSS com teto ~1,8% a.m.; CLT ordem de 3–4% a.m. | CNPS; mercado | Conferir |
| Cheque especial | Teto de 8% a.m.; na base ~1,3% a.m. sobre o saldo negativo | Res. CMN 4.765; base | Confirmada |
| Inadimplência do rotativo | 65% | BC jul/26 | Confirmada |
| Inadimplência das famílias | 5,8% | BC jul/26 | Confirmada |
| Perda em caso de calote, interchange, funding | Parâmetros | Não públicos | DESCONHECIDO |
| Elegibilidade a cada produto | Regra do banco | Não temos | DESCONHECIDO; a demo assume |
| Aceite do plano | Hipótese do piloto | Nenhuma | Métrica, não promessa |

Exemplo com R$ 1.000 não pagos por um mês: no rotativo o banco fatura R$ 140 no papel; com 65% de inadimplência, a expectativa fica em torno de R$ 49, com risco sobre o principal. Numa parcela a 6% a.m. com inadimplência perto da média das famílias, a expectativa fica em torno de R$ 57, com o principal voltando. Menos juro por real, mais dinheiro recebido, mais cliente que fica. Todas as taxas de produto entram como parâmetro em `config/taxas.yaml`; nenhuma sai do LLM.

## MVP e futuro

**MVP (demo de 27/09):** um cliente do grupo B; sinal na tela de pagamento; consentimento; diagnóstico; duas saídas com custo; plano com teto; dois meses seguintes simulados; painel "como cheguei aqui" para a banca.

**Futuro (uma frase no pitch):** Open Finance para enxergar aperto em outros bancos; crédito com garantia; recompensa por comportamento (pontos por fatura inteira, não por gasto); cartões parceiros; não correntistas.

Fora: cashback por categoria, Itaú Shop como remédio, gamificação por gasto, acesso delegado a familiar, alerta estilo Defesa Civil, seguro embutido como condição do crédito.

## Métricas

- Aceite: recomendações feitas x aceitas.
- Saída: clientes que completam 3 faturas inteiras seguidas; meses rolados por cliente antes e depois.
- Fluxo: entradas em C por mês (KPI do time: reduzir quem rola mais de 5 vezes no ano; hoje 204).
- Banco: PDD evitada; receita recebida por real; churn do grupo.
- Compreensão: antes de confirmar, o cliente sabe dizer quanto a escolha custa (sim/não na tela).

## Mapa: pedido da staff → resposta

| Pedido | Resposta |
|---|---|
| Diferencial vs parcelamento e vs ia.i | O app oferece parcelar esta fatura; o ia.i responde perguntas. Este diagnostica a causa, monta o plano e volta todo mês. Teste ao vivo da mentora: o ia.i não sugeriu plano |
| Agente, não simulador | Age por sinal do cliente, com consentimento; segue por 3 faturas |
| Determinístico vs LLM | Sinal, triagem, cálculo e acompanhamento em código; LLM só explica e pergunta |
| P&L do banco | Fórmula acima com 65% de inadimplência como fato central |
| Público não só inadimplente | Ninguém aqui está inadimplente; 69% da base rola e paga |
| Guardrails próprios | Nenhum número sai do LLM; sem crédito para quem não cabe; humano no C; sem seguro embutido |
| Impacto social | O C entra com renegociação e humano |
| Confiança e Resolução 8 | Sem venda casada, sem push, linguagem simples, plano do lado do cliente |
| Regulação que vem | O BC quer fricção antes do crédito caro e opção adequada mais clara; o agente é isso |

## Pitch em cinco slides

1. A dor: 69% rolam a fatura; quatro causas; um cliente real mês a mês.
2. O problema de design: rotativo com 65% de inadimplência; o banco também perde.
3. Cabe no Bolso: sinal, causa, urgência, plano, teto, acompanhamento. Demo no QR code.
4. A conta fecha: cliente paga menos, banco recebe mais, B não vira C.
5. Piloto: 384 clientes do B, métricas, MVP e futuro.

Fontes: [Galípolo, 65% de inadimplência](https://clickpetroleoegas.com.br/galipolo-banco-central-credito-predatorio-endividamento-mhbb01) · [BC jul/26](https://www.letsmoney.com.br/noticias/inadimplencia-recorde-rotativo-436-bc-julho-2026/) · [BC jan/26](https://agenciabrasil.ebc.com.br/economia/noticia/2026-02/juros-subiram-para-familias-e-empresas-em-janeiro-mostra-bc) · [Peic/CNC jul/26](https://agenciabrasil.ebc.com.br/economia/noticia/2026-08/cnc-endividamento-das-familias-sobe-para-82-mas-inadimplencia-cai) · [Idec, teto de juros](https://idec.org.br/dicas-e-direitos/teto-de-juros-do-cartao-de-credito-conheca-mudancas-nos-limites-do-rotativo) · [Lei 14.690/2023](https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/l14690.htm) · [Canaltech, ia.i](https://canaltech.com.br/apps/nova-ia-do-itau-mostra-para-onde-vai-o-seu-dinheiro-e-te-ajuda-a-controlar-gastos/)
