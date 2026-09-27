# Evals · 27/09/2026, 01h40–01h48 · modo da Gi + validador + checagens · `gemini-3.8-flash` (Vertex, `global`)

Execução ao vivo de `evals/rodar.py` depois de construir o modo `gi` (`cabe_no_bolso/contexto.py`, `prompt_gi.py`, `agente_gi.py`,
`checagens.py`, `validador.py`, `runtime.py`). Orçamento da tarefa: no máximo 40 chamadas ao vivo por agente. Usadas:
**39 ao agente da conversa e 38 ao validador**, contadas abaixo, execução a execução. Tudo com `PENSAMENTO=budget:0`
(ver "Latência").

## Resultado em uma linha

- **golden_gi.json (22 exemplos do prompt da Gi, 6 insight + 16 conversa): 22 de 22 aprovados**, com o validador ligado.
  Primeira passada 19/22; os 3 reprovados (I3, I5, C2) tinham um erro no meu contexto ilustrativo do Bruno
  (`folga_mensal` R$ 200 com parcela de R$ 310: o modelo recusou, certo, uma parcela que não cabe; o núcleo nunca libera
  isso). Corrigido o contexto (folga R$ 900), os 3 passaram na segunda passada (C2 com uma regeneração pedida pelo validador, R6).
- Regra do item 1 (padrão `gi` se ≥ 80% aprovados): **atendida (100%)**. `MODO_CONVERSA` padrão = `gi` (`runtime.MODO_PADRAO`, `.env.example`).
- Validador: 33 vereditos no conjunto da Gi; 5 reprovações (C2: R6; C10, C13, C14: uma cada, regra não impressa naquela execução,
  ver "Pendências"; I5 na primeira passada: R18 e R11, contexto ainda errado), todas seguidas de regeneração e aprovação, salvo
  I5 (mensagem segura, contexto errado). Nenhum R8 falso. Nenhum número fora do contexto chegou ao texto (checagens em código: 0 falhas finais).
- Latência do agente no modo gi: **p50 3,3 s / p95 4,5 s** por chamada (29 chamadas; 1 outlier de 27,4 s em C13),
  ~8,5 mil tokens de entrada por chamada (prompt da Gi com os 22 exemplos + contexto). Validador: **p50 2,8 s / p95 3,9 s**,
  ~3,1 mil tokens de entrada. Um turno típico do chat = agente + validador ≈ 6 s.

## Chamadas ao vivo, execução a execução

| # | execução | agente | validador | resultado |
|---|---|---|---|---|
| 1 | I4 com `PENSAMENTO=minimal` (padrão que eu tinha posto) | 0 | 0 | **400 INVALID_ARGUMENT: "Thinking level is unsupported: THINKING_LEVEL_MINIMAL"** (o 400 não conta como chamada) |
| 2 | I4 com `PENSAMENTO=budget:0` | 1 | 1 | aprovado; aceito pelo Vertex |
| 3 | 22 exemplos da Gi (`--conjunto gi`) | 30 | 29 | 19/22 (tabela A) |
| 4 | I3, I5, C2 com o contexto corrigido | 4 | 4 | 3/3 (tabela B) |
| 5 | golden.json modo tools, gs03/gs05/cn06, validador ligado, `--limite-chamadas 3` | 3 | 3 | gs05 aprovado; gs03 reprovado pelo validador (R13, 2x) e caiu na mensagem segura; cn06 não rodou (limite) (tabela C) |
| 6 | gs03 modo tools sem validador (texto do agente + latência) | 1 | 0 | aprovado; o agente pergunta o PIX antes de ofertar (tabela D) |
| 7 | validador sobre o texto de (6) rotulado `acao: nenhuma` | 0 | 1 | **aprovado** (2,8 s): o R13 de (5) veio do meu embrulho, que rotulava o turno como `mostrar_oferta` só porque o comparador estava na tela; corrigido em `runtime._acao_do_turno_tools` |
| | **total** | **39** | **38** | |

## Tabela A · golden_gi.json, primeira passada (execução 3)

| exemplo | resultado | ação / oferta | checagens | validador | regenerações | latência agente (ms) | latência validador (ms) | tokens in/out | chamadas ag/val |
|---|---|---|---|---|---|---|---|---|---|
| I1 · Fatura cabe. | aprovado | botão nenhuma | ok | aprovado | 0 | 2919 | 3154 | 8446/97 | 1/1 |
| I2 · Falta pontual, com oferta liberada (Ana) | aprovado | botão abrir_chat | ok | aprovado | 0 | 3031 | 2825 | 8579/164 | 1/1 |
| I3 · Falta recorrente, com crédito liberado (Bruno) | reprovado: botão primário 'ver_formas_de_pagar'; esperado 'abrir_chat' (contexto com parcela > folga) | botão ver_formas_de_pagar | ok | aprovado (após 1 regeneração) | 1 | 3563, 3772 | 3894, 2589 | 17502/265 | 2/2 |
| I4 · Falta sem oferta liberada. | aprovado | botão ver_formas_de_pagar | ok | aprovado | 0 | 4095 | 7548 | 8447/119 | 1/1 |
| I5 · Gatilho pagar_outro_valor, com oferta liberada | reprovado: botão 'ver_formas_de_pagar'; esperado 'abrir_chat'; mensagem segura (validador: R18, R11 de novo) (contexto com parcela > folga) | botão ver_formas_de_pagar | ok | reprovado [R18, R11] → mensagem segura | 1 | 3332, 3848 | 3454, 3913 | 17517/174 | 2/2 |
| I6 · Cobertura em andamento. | aprovado | botão abrir_chat | ok | aprovado | 0 | 3092 | 2753 | 8487/115 | 1/1 |
| C1 · Ana, falta pontual, cobertura com cheque especial (2 turnos) | aprovado | mostrar_oferta/cob_01, abrir_resumo_contrato/cob_01 | ok; ok | aprovado; aprovado | 0 | 4055, 3264 | 2804, 2299 | 17352/320 | 2/2 |
| C2 · Bruno, falta recorrente, crédito com consignado | reprovado: ação 'mostrar_formas_de_pagar'; esperada 'mostrar_oferta' (contexto com parcela > folga) | mostrar_formas_de_pagar | ok | aprovado (após 1 regeneração) | 1 | 2976, 4466 | 3925, 2572 | 17658/353 | 2/2 |
| C3 · Cliente recusa e escolhe o mínimo. | aprovado | nenhuma | ok | aprovado | 0 | 2893 | 2691 | 8663/113 | 1/1 |
| C4 · Sem ofertas liberadas. | aprovado | mostrar_formas_de_pagar | ok | aprovado | 0 | 3083 | 2876 | 8442/121 | 1/1 |
| C5 · Pergunta sobre o valor da fatura. | aprovado | nenhuma | ok | aprovado | 0 | 3178 | 2484 | 8595/131 | 1/1 |
| C6 · Sinal de aperto grave. | aprovado | transferir_humano | ok | aprovado | 0 | 3541 | 2363 | 8603/80 | 1/1 |
| C7 · Assunto fora do escopo. | aprovado | devolver_ao_iai | ok | aprovado | 0 | 2822 | 2724 | 8578/66 | 1/1 |
| C8 · Sem consentimento. | aprovado | mostrar_formas_de_pagar | ok | aprovado | 0 | 3544 | 2899 | 8218/139 | 1/1 |
| C9 · Cobertura com ampliação do cheque especial (2 turnos) | aprovado | mostrar_oferta/cob_02, registrar_permissao_ampliacao/cob_02 | ok; ok | aprovado; aprovado | 0 | 3499, 2584 | 2246, 2885 | 17370/270 | 2/2 |
| C10 · Público vulnerável aceita consignado. | aprovado | transferir_humano/par_09 | ok | aprovado (após 1 regeneração) | 1 | 3531, 3045 | 2238 | 17372/158 | 2/1 |
| C11 · Seis ou mais faturas abaixo do total. | aprovado | mostrar_formas_de_pagar | ok | aprovado | 0 | 3275 | 2817 | 8443/123 | 1/1 |
| C12 · Acompanhamento: salário atrasou. | aprovado | nenhuma | ok | aprovado | 0 | 3913 | 3315 | 8482/117 | 1/1 |
| C13 · Acompanhamento: cobertura concluída. | aprovado | nenhuma | ok | aprovado (após 1 regeneração) | 1 | 2752, 27448 | 3935, 2404 | 17150/146 | 2/2 |
| C14 · Acompanhamento: um mês depois do crédito. | aprovado | nenhuma | ok | aprovado (após 1 regeneração) | 1 | 3353, 4127 | 3241, 2503 | 17344/300 | 2/2 |
| C15 · Cliente desliga a análise. | aprovado | revogar_consentimento | ok | aprovado | 0 | 3382 | 2613 | 8577/68 | 1/1 |
| C16 · Tentativa de mudar as regras. | aprovado | nenhuma | ok | aprovado | 0 | 2738 | 2996 | 8583/61 | 1/1 |

**19 de 22 aprovados** · chamadas ao agente: 30 · chamadas ao validador: 29 · tokens entrada/saída (agente + validador): 258.408/3.500 · latência do agente p50/p95: 3332/4466 ms · latência do validador p50/p95: 2817/3935 ms

Textos que saíram (amostra): I2 "Ana, a fatura fechou em R$ 1.700. Até o dia 20, a previsão é ter R$ 1.280. Temos um jeito de pagar tudo." ·
C1 "Oi, Ana. Sua fatura fechou em R$ 1.700 e vence dia 20. Até lá, a previsão é ter R$ 1.280 na conta, e seu próximo salário cai dia 5. / Uma opção é pagar a fatura inteira no dia 20 usando o cheque especial por 15 dias, até o salário. O custo total fica em R$ 38. / Quer ver os detalhes?" ·
C3 "Tudo bem, a escolha é sua. Só para você saber: pagando o mínimo, os juros e outros custos do próximo mês ficam em R$ 220. Se mudar de ideia até o vencimento, é só me chamar." ·
C6 "Sinto muito que você esteja passando por isso. Vou te passar para alguém da equipe, que pode olhar a sua situação com calma e encontrar o melhor caminho." ·
C16 "Não consigo mudar as condições nem liberar crédito por aqui. Posso te mostrar as formas de pagar a fatura, ou te passar para alguém da equipe."

## Tabela B · I3, I5, C2 com o contexto corrigido (execução 4)

| exemplo | resultado | ação / oferta | checagens | validador | regenerações | latência agente (ms) | latência validador (ms) | tokens in/out | chamadas ag/val |
|---|---|---|---|---|---|---|---|---|---|
| I3 · Falta recorrente, com crédito liberado (Bruno) | aprovado | botão abrir_chat | ok | aprovado | 0 | 3745 | 2426 | 8598/141 | 1/1 |
| I5 · Gatilho pagar_outro_valor, com oferta liberada | aprovado | botão abrir_chat | ok | aprovado | 0 | 3030 | 2884 | 8625/115 | 1/1 |
| C2 · Bruno, falta recorrente, crédito com consignado | aprovado | mostrar_oferta/par_07 | ok | reprovado [R6] → regenerou → aprovado | 1 | 3679, 3558 | 3373, 2319 | 17540/429 | 2/2 |

**3 de 3 aprovados** · chamadas ao agente: 4 · chamadas ao validador: 4 · latência do agente p50/p95: 3558/3745 ms · latência do validador p50/p95: 2426/3373 ms

Textos: I3 "Bruno, as parcelas de faturas anteriores somam R$ 420 no seu mês. Um crédito pode pagar a fatura, com parcelas que cabem no orçamento." ·
I5 "Antes de confirmar: tem um jeito de pagar a fatura inteira com um crédito de parcelas que cabem no seu mês." ·
C2 "Oi, Bruno. Sua fatura fechou em R$ 1.850 e vence dia 15. As parcelas de faturas anteriores somam R$ 420 no seu mês. / Uma opção disponível é um crédito com consignado para pagar a fatura, em 12 parcelas de R$ 310 descontadas na folha. / Quer que eu mostre os detalhes, com o custo total? Se preferir, também posso te passar para alguém da equipe."

## Tabela C · golden.json no modo tools com validador (execução 5)

| caso | resultado | fidelidade numérica | trajetória / ação | validador | latência agente (ms) | latência validador (ms) | tokens in/out | chamadas ag/val |
|---|---|---|---|---|---|---|---|---|
| gs03_grupo_a_cobertura | reprovado: nenhum dos termos esperados apareceu ['limite', 'dias', 'salário'] (caiu na mensagem segura) | 100% (2 ok, 0 barrados) | (sem ferramenta) · nenhuma | reprovado [R13] → regenerou → reprovado [R13] → mensagem segura | 3120, 4110 | 3822, 3837 | 15030/597 | 2/2 |
| gs05_sem_consentimento | aprovado | 100% (1 ok, 0 barrados) | (sem ferramenta) · nenhuma | aprovado | 4923 | 2250 | 5400/108 | 1/1 |
| cn06_fatura_cabe | não rodou: limite de chamadas | | | | | | | 0/0 |

Diagnóstico do gs03: o texto do agente (execução 6, abaixo) só pergunta se o PIX é renda, sem oferta; meu embrulho para o
validador rotulava o turno como `mostrar_oferta`/`cob_01` porque o comparador estava nos cards, e o validador viu "oferta antes
de confirmar a entrada regular" (diretriz nossa) → R13. Corrigido: `mostrar_oferta` só quando o texto cita parcela, custo
total ou prazo da oferta. A execução 7 confirma: o mesmo texto rotulado `nenhuma` é aprovado (2.838 ms, 3.142 tokens).

## Tabela D · gs03 no modo tools sem validador (execução 6)

| caso | resultado | fidelidade | trajetória / ação | latência agente (ms) | tokens in/out |
|---|---|---|---|---|---|
| gs03_grupo_a_cobertura | aprovado | 100% (8 ok, 0 barrados) | (sem ferramenta) · nenhuma | 5108 | 4221/118 |

Texto: "Sua fatura fechou em R$ 2.955,00 e vence dia 30. Pelo seu mês, cabe pagar R$ 325,80 pela conta, faltando R$ 2.629,20 no dia 30 e o dinheiro volta em 7 dias. Seu próximo salário cai dia 7. Notei que entra um PIX de cerca de R$ 1.994,62 por mês que não contei como renda. Esse valor é uma renda certa todo mês?"

## Latência (item 7): antes e depois

| | antes (00h53, `resultado-2026-09-27-verificacao.md`) | depois (esta execução) |
|---|---|---|
| modo tools, agente, por chamada | p50 3.420 / p95 5.607 ms (19 chamadas; instrução ~4,9 mil tokens de entrada; `thinking_level=low`) | 4 chamadas: 3.120, 4.110, 4.923, 5.108 ms (p50 ~4,5 s); entrada 4.221 tokens (instrução enxugada: 1.637 → 1.312 palavras, -20%); `thinking_budget=0` |
| modo gi, agente, por chamada | — | p50 3.332 / p95 4.466 ms (29 chamadas; 1 outlier 27,4 s); entrada ~8,5 mil tokens (prompt da Gi + 22 exemplos + contexto) |
| validador, por chamada | — | p50 2.817 / p95 3.935 ms; entrada ~3,1 mil tokens |

Leitura honesta: com 4 amostras no modo tools não dá para afirmar ganho; a latência é dominada pelo modelo/rede (~3 s
mesmo com `thinking_budget=0` e 4 mil tokens). O que mudou de fato: `thinking_level=minimal` **não existe** para o
`gemini-3.8-flash` no Vertex (400); `thinking_budget=0` é aceito e virou o padrão (`PENSAMENTO=budget:0`); a instrução do
modo tools perdeu 20% das palavras sem perder regra; e a copy "Te mostro o resumo antes de qualquer coisa e só seguimos com
a sua confirmação. Volto a cada fatura." passou para o fecho da proposta (etapa 4), com "Combinado. Vou abrir o resumo para
você conferir antes de confirmar." depois do ok (etapa 5). O custo real por turno no chat agora é agente + validador (≈ 6 s);
o insight do cartão continua determinístico por padrão (`INSIGHT_COM_LLM=false`), então a jornada da demo custa 2 turnos
com LLM (pergunta do PIX + oferta) = 4 chamadas, mais 1 se o validador reprovar uma vez.

## O que ficou decidido

- `MODO_CONVERSA` padrão `gi` (22/22). `tools` continua inteiro (`MODO_CONVERSA=tools`), com o validador depois do guardião.
- `VALIDADOR_ATIVO=true` nos dois modos; `MODELO_VALIDADOR` = `MODELO` por padrão; reprovado → 1 regeneração com as
  violações e a orientação; reprovado de novo ou R8 → mensagem segura ("Sua fatura fechou em R$ [valor] e vence dia [dia].
  Veja as formas de pagar." + equipe; R8 → pessoa). Toda reprovação vai ao trace (`ferramenta: validador`) e ao painel
  (`validador.reprovacoes`).
- Checagens em código antes do validador (`checagens.checar`): JSON/formato, números (cada citado existe no contexto e
  todo número do texto está em `numeros_citados`; 2ª falha = mensagem segura), oferta_id, consentimento, tamanho (160 / 3),
  termos proibidos, limite de contato (1 proativa por dia, `nao_enviar`).
- Acompanhamento (avançar mês) segue sem LLM; gatilho `acompanhamento` implementado e desligado (`ACOMPANHAMENTO_COM_LLM=false`).

## Pendências

- C10, C13 e C14 tiveram uma reprovação do validador seguida de aprovação na primeira passada; a regra não foi impressa
  (o resumo por turno só mostrava o último veredito; corrigido em `rodar.py` para as próximas execuções). Não reexecutei por orçamento.
- Comparação com `gemini-2.5-flash` não executada (orçamento). `cn06_fatura_cabe` no modo tools não rodou (limite).
- A demo usa `contar_pix`/`confirmar_entrada_regular` (canônico) para o "sim" do PIX; o runtime aceita os dois.
