# 11 · GenAI FinOps

Princípio: **custo por conversa é métrica de produto, não rodapé de infraestrutura.** O agente só chama o modelo onde há conversa; tudo que é número é código; cada chamada é medida e aparece no painel da banca. Preços e limites vivem em `config/finops.yaml`, com fonte e data.

## 1. Onde o dinheiro vai

| Componente | Quando cobra | Como limitamos |
|---|---|---|
| Gemini 3.8 Flash (Agent Platform) | por token de entrada e saída, só em respostas 200 | 1 chamada do agente + 1 do validador por turno, no máximo 1 regeneração; thinking mínimo; instrução enxuta; acompanhamento mensal sem LLM; insight do card uma vez por sessão |
| Cloud Run | por vCPU-s e GiB-s ativos; instância mínima cobra ociosa a 1/10 do preço | `max-instances=1`, `concurrency=40`, `min-instances=1` só no dia da demo (fora dela, 0) |
| BigQuery | por byte consultado | uma consulta por cliente por sessão sobre views agregadas; `maximum_bytes_billed` de 1 GB; CSV em dev e como plano B |
| Cloud Build e Artifact Registry | por minuto de build e GB armazenado | build só por commit; imagem de ~200 MB |
| Antigravity, Gemini CLI, Cloud Shell | do mesmo orçamento de US$ 1.000 do grupo | não usar em loop; evals com teto de chamadas |

## 2. Modelo de custo

Preço vigente (introdutório até 31/12/2026): **US$ 0,75 por milhão de tokens de entrada e US$ 3,75 por milhão de saída**; a partir de 01/01/2027, US$ 1,50 e US$ 7,50 ([Agent Platform pricing](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing)).

```
custo_sessao = tokens_entrada/1e6 × 0,75 + tokens_saida/1e6 × 3,75
```

Medido ao vivo em 27/09/2026 (`agent/evals/resultado-2026-09-27-verificacao.md`), modo com tools, sem validador:

| Sessão | Chamadas | Tokens entrada / saída | p50 / p95 | Custo |
|---|---:|---:|---:|---:|
| Bruno (Rolando) | 3 | 15.483 / 352 | 2,7 s / 4,0 s | US$ 0,013 |
| Ana (Escorregão) | 3 | 14.618 / 248 | 2,7 s / 3,4 s | US$ 0,012 |

Com o validador da Gi em cada turno (mesmo contexto na entrada, saída curta), a sessão fica na casa de **US$ 0,025**; com o preço de 2027, **US$ 0,05**. O piloto de 384 clientes do grupo Rolando, uma conversa por cliente e acompanhamento sem LLM, custa em torno de **US$ 10 por ciclo de fatura** em modelo. Esses são os números para o slide "a conta fecha": o custo de conversar é centavos por cliente; o juro que sai do rotativo é dezenas de reais.

Cloud Run na demo: 1 vCPU e 1 GiB com instância mínima ligada custam cerca de US$ 0,43 por dia ociosos ([Cloud Run pricing](https://cloud.google.com/run/pricing)); fora da demo, `min-instances=0` e o custo vai a zero.

## 3. Travas (o que impede a conta de subir)

| Trava | Onde |
|---|---|
| Nenhum número sai do LLM; cálculo em código | `cabe_core`, guardião em `cabe_no_bolso/callbacks.py` |
| Acompanhamento mensal determinístico | `cabe_core/acompanhar.py`; `acompanhamento_com_llm: false` |
| Teto de chamadas por turno e por sessão | `cabe_no_bolso/runtime.py`, `server/main.py` |
| Teto de tool calls por turno | `before_tool` |
| Thinking mínimo e instrução enxuta | `generate_content_config`, `instruction.md` |
| Sessão em memória, 1 instância, concorrência 40 | `deploy/deploy.sh` |
| Rate limit por IP, tamanho de corpo, timeout | `server/limites.py` |
| BigQuery: views agregadas, 1 consulta por sessão, bytes cobrados limitados | `cabe_core/dados.py` |
| Orçamento do grupo com alertas em 50 / 80 / 100% | Cloud Billing (`deploy/orcamento.sh`) |

## 4. Medição

- **Por sessão:** `GET /api/painel/{sessao}` devolve chamadas, tokens de entrada e saída, latência p50 e p95 e `custo_estimado` calculado com `config/finops.yaml`.
- **Por turno:** o trace registra cada chamada ao modelo (agente, validador, regeneração), o veredito do validador e o tempo.
- **Por serviço:** Cloud Logging estruturado (JSON, sem PII) com `sessao_id`, `chamadas_llm`, `tokens`, `latencia_ms`; métricas baseadas em log para custo diário e latência p95.
- **Por agente (alvo):** `BigQueryAgentAnalyticsPlugin` do ADK grava eventos do agente num dataset `agent_logs`, o que permite custo por cliente, por cenário e por dia com SQL.

## 5. Decisões de escala (o que muda de 384 para milhões)

| Escala | O que muda | Por quê |
|---|---|---|
| Piloto (384) | nada além do que está | US$ 10 por ciclo |
| ia.i inteiro (milhões) | context caching da instrução e dos exemplos (até 90% de desconto no cacheado); validador por amostragem estatística em vez de 100%, ou num modelo menor; batch para o insight do card no fechamento da fatura (50% de desconto); ficha do cliente pré-calculada na gold | o custo é dominado pelos tokens de entrada repetidos; o validador dobra as chamadas |

## 6. Checklist do dia

1. `min-instances=1` só depois do deploy final; voltar a 0 depois do pitch.
2. Evals ao vivo com teto de 40 chamadas por rodada; comparação de modelos uma vez.
3. Conferir o consumo do orçamento no Cloud Billing antes do pitch e anotar no painel.
4. Não rodar Antigravity nem Gemini CLI em loop.
