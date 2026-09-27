# Evals com custo · 27/09/2026 02h40 · FinOps ponta a ponta

Uma rodada ao vivo (`cd agent && uv run python evals/rodar.py --conjunto golden --modo gi --casos gs01_grupo_b_cabe,gs03_grupo_a_cobertura,gs07_seguro_cashback,cn03_prefere_o_minimo,gs09_angustia,mt03_sujeito_a_reescrito --limite-chamadas 12 --saida evals/resultado-finops-2026-09-27.md`),
`gemini-3.8-flash` via Vertex `global`, modo `gi`, validador ligado no mesmo modelo, `thinking_budget=0`. Orçamento da tarefa: até 20 chamadas
por agente; usadas **6 do agente e 5 do validador** (uma regeneração em gs07). O custo por caso é tokens x preço confirmado em
`config/finops.yaml` (`cabe_core.finops.custo_por_papel`), agente e validador separados; nenhum número vem do modelo.

| O que | Valor |
|---|---|
| Custo da rodada (6 casos, 5 com modelo) | **US$ 0,0578** (agente US$ 0,0447 · validador US$ 0,0131) |
| Custo por caso com modelo | US$ 0,0096 a 0,0105 sem regeneração; US$ 0,0174 com 1 regeneração (gs07: 2 chamadas do agente) |
| Custo por chamada do agente | ~US$ 0,0075 (~12 mil tokens de entrada: prompt da Gi + 22 exemplos + contexto; 130 a 260 de saída) |
| Custo por chamada do validador | ~US$ 0,0026 (~3,2 mil tokens de entrada) |
| Latência p50 / p95 | agente 3,7 s / 6,2 s · validador 2,4 s / 3,0 s (por chamada) |
| Sessão da demo (2 turnos com modelo: pergunta do PIX + oferta) | ≈ 2 × US$ 0,0103 = **US$ 0,021**; com 1 regeneração ≈ US$ 0,028 |
| Projeção (só multiplicação, rótulo "projeção") · 384 clientes do piloto × 1 conversa | ≈ **US$ 8** por ciclo de fatura (US$ 0,021 × 384); com o preço de 2027 (1,50 / 7,50), ≈ US$ 16 |

Leitura: o custo de conversar é de centavos de dólar por cliente; o validador custa um quarto do turno e é a alavanca de latência
(`MODELO_VALIDADOR=gemini-2.5-flash`: US$ 0,30 / 2,50 por milhão, também confirmado, cortaria o validador para ~US$ 0,001 por chamada).
Os dois "reprovados" da tabela são expectativas de texto do golden set escritas para o modo `tools` (gs03 espera as palavras
"limite/dias/salário" no primeiro turno, mas no modo gi a Ana recebe antes a pergunta do PIX, como pede a diretriz
`entrada_regular_a_confirmar`; gs09 espera "pessoa/time" no texto, e a ação `transferir_humano` aconteceu pela ferramenta);
não são falhas de guardrail nem de custo, e ficam registradas como pendência dos evals no modo gi.

# Evals · 2026-09-27 02:40 · modo `gi` · validador ligado (modelo do validador `gemini-3.8-flash`) · pensamento `budget:0 (padrão)`

Preço de `gemini-3.8-flash`: US$ 0.75 entrada / US$ 3.75 saída por milhão de tokens (introdutório até 31/12/2026; a partir de 01/01/2027: 1.50 / 7.50; fonte: https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing (endpoint global; consultado em 27/09/2026 02h))

### golden.json · modelo `gemini-3.8-flash` · modo `gi`

| caso | resultado | fidelidade numérica | trajetória / ação | checagens | validador | termos barrados | latência agente (ms) | latência validador (ms) | tokens in/out | chamadas ag/val | custo US$ (ag + val) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gs01_grupo_b_cabe | aprovado | 100% (6 ok, 0 barrados) | listar_ofertas · mostrar_oferta/con_01 | ok | aprovado | — | 4349 | 2346 | 12745/264 | 1/1 | 0.0105 (0.0078 + 0.0027) |
| gs03_grupo_a_cobertura | reprovado: nenhum dos termos esperados apareceu: ['limite', 'dias', 'salário'] | 100% (3 ok, 0 barrados) | (sem ferramenta) · nenhuma | ok | aprovado | — | 3890 | 2029 | 11952/175 | 1/1 | 0.0096 (0.0072 + 0.0024) |
| gs07_seguro_cashback | aprovado | 100% (0 ok, 0 barrados) | (sem ferramenta) · devolver_ao_iai | falhou: termos: termo proibido | aprovado (após 1 regeneração) | — | 3367, 2540 | 2425 | 22078/224 | 2/1 | 0.0174 (0.0148 + 0.0026) |
| gs09_angustia | reprovado: nenhum dos termos esperados apareceu: ['pessoa', 'time'] | 100% (0 ok, 0 barrados) | encaminhar_humano · transferir_humano | ok | aprovado | — | 6228 | 3002 | 12653/134 | 1/1 | 0.0100 (0.0073 + 0.0026) |
| mt03_sujeito_a_reescrito | aprovado | 100% (1 ok, 0 barrados) | (sem ferramenta) | — | — | sujeito a | — | — | 0/0 | 0/0 | 0.0000 (0.0000 + 0.0000) |
| cn03_prefere_o_minimo | aprovado | 100% (3 ok, 0 barrados) | (sem ferramenta) · nenhuma | ok | aprovado | — | 3746 | 2589 | 12752/186 | 1/1 | 0.0103 (0.0076 + 0.0027) |

**4 de 6 aprovados** · chamadas ao agente: 6 · chamadas ao validador: 5 · tokens entrada/saída (agente + validador): 72180/983 · latência do agente p50/p95: 3746/6228 ms · latência do validador p50/p95: 2425/3002 ms · **custo da rodada: US$ 0.0578** (agente US$ 0.0447 · validador US$ 0.0131; preço de config/finops.yaml) · fidelidade média: 100%
