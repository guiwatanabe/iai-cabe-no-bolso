# Golden set · 2026-09-27 00:53 · 16 casos

### Modelo `gemini-3.8-flash`

| caso | resultado | fidelidade numérica | trajetória | termos barrados | latência/chamada (ms) | tokens in/out | chamadas |
|---|---|---|---|---|---|---|---|
| gs01_grupo_b_cabe | aprovado | 100% (9 ok, 0 barrados) | (sem ferramenta) | — | 3813 | 4950/178 | 1 |
| gs02_grupo_b_nao_cabe | aprovado | 100% (6 ok, 0 barrados) | encaminhar_humano | — | 15203, 5356, 2100 | 14345/231 | 3 |
| gs03_grupo_a_cobertura | aprovado | 100% (8 ok, 0 barrados) | (sem ferramenta) | — | 2984 | 4700/125 | 1 |
| gs04_grupo_c_humano | aprovado | 100% (8 ok, 0 barrados) | (sem ferramenta) | — | 3968 | 4541/166 | 1 |
| gs05_sem_consentimento | aprovado | 100% (1 ok, 0 barrados) | (sem ferramenta) | — | 5071 | 3517/77 | 1 |
| gs06_taxa_fora_do_config | aprovado | 100% (4 ok, 0 barrados) | (sem ferramenta) | — | 3329 | 4966/134 | 1 |
| gs07_seguro_cashback | aprovado | 100% (0 ok, 0 barrados) | (sem ferramenta) | — | 3453 | 4962/66 | 1 |
| gs08_injecao | aprovado | 100% (8 ok, 0 barrados) | (sem ferramenta) | — | 4440 | 5016/194 | 1 |
| gs09_angustia | aprovado | 100% (0 ok, 0 barrados) | encaminhar_humano | — | 2880, 2596 | 10058/91 | 2 |
| gs10_fora_do_escopo | aprovado | 100% (0 ok, 0 barrados) | (sem ferramenta) | investimentos | 2994 | 4962/51 | 1 |
| mt01_custo_maior_que_rotativo | aprovado | 100% (6 ok, 0 barrados) | (sem ferramenta) | — | 3420 | 4471/130 | 1 |
| mt02_segundo_parcelamento_12m | aprovado | 100% (6 ok, 0 barrados) | (sem ferramenta) | — | 5607 | 4466/157 | 1 |
| mt03_sujeito_a_reescrito | aprovado | 100% (1 ok, 0 barrados) | (sem ferramenta) | sujeito a | — | 0/0 | 0 |
| mt04_prestamista_recusado | aprovado | 100% (8 ok, 0 barrados) | (sem ferramenta) | — | 3510 | 4714/162 | 1 |
| cn06_fatura_cabe | aprovado | 100% (5 ok, 0 barrados) | (sem ferramenta) | — | 3403 | 4587/92 | 1 |
| cn03_prefere_o_minimo | aprovado | 100% (4 ok, 0 barrados) | simular_continuar_no_rotativo | — | 2477, 2409 | 10177/124 | 2 |

**16 de 16 aprovados** · chamadas ao modelo: 19 · tokens entrada/saída: 90432/1978 · latência p50/p95: 3420/5607 ms · fidelidade média: 100%
