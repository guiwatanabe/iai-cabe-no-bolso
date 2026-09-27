# 11 · GenAI FinOps

Princípio: **custo por conversa é métrica de produto, não rodapé de infraestrutura.** O agente só chama o modelo onde há conversa; tudo que é número é código; cada chamada é contada, medida e precificada com o preço oficial, e aparece no painel da banca, no log e nos evals. Preços, piloto e limites vivem em `config/finops.yaml`, com fonte e data; quem lê é `agent/cabe_core/finops.py` (funções puras). Nenhum custo nasce no LLM nem na tela: é `tokens × preço do YAML`.

## 1. Onde o dinheiro vai

| Componente | Quando cobra | Como limitamos |
|---|---|---|
| Gemini 3.8 Flash (Agent Platform, endpoint `global`) | por token de entrada e saída, só em respostas 200 | 1 chamada do agente + 1 do validador por turno, no máximo 1 regeneração; `thinking_budget=0`; instrução enxuta; acompanhamento mensal sem LLM; insight do card em código por padrão; teto de 12 chamadas por sessão e de 8 ferramentas por turno |
| Cloud Run | por vCPU-s e GiB-s ativos; instância mínima cobra ociosa a 1/10 do preço | `max-instances=1`, `concurrency=40`, `min-instances=1` só no dia da demo (fora dela, 0) |
| BigQuery | por byte consultado | uma consulta por cliente por sessão sobre views agregadas; `maximum_bytes_billed` de 1 GB; CSV em dev e como plano B |
| Cloud Build e Artifact Registry | por minuto de build e GB armazenado | build só por commit; imagem de ~200 MB |
| Antigravity, Gemini CLI, Cloud Shell | do mesmo orçamento de US$ 1.000 do grupo | não usar em loop; evals com teto de chamadas por agente |

## 2. Modelo de custo

Preços confirmados na [página oficial da Agent Platform](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing) em 27/09/2026 e gravados em `config/finops.yaml` (`precos`, com `fonte`, `vigencia` e `status: confirmada`):

| Modelo | Entrada (US$/1M tokens) | Saída (US$/1M tokens) | Vigência |
|---|---:|---:|---|
| `gemini-3.8-flash` (agente; validador por padrão) | 0,75 | 3,75 | introdutório até 31/12/2026; a partir de 01/01/2027: 1,50 / 7,50 |
| `gemini-2.5-flash` (alternativa para o validador) | 0,30 | 2,50 | preço padrão, sem mudança anunciada |

```
custo_papel  = tokens_entrada_papel/1e6 × preço_entrada(modelo_papel) + tokens_saida_papel/1e6 × preço_saida(modelo_papel)
custo_sessao = custo_agente + custo_validador          (cabe_core.finops.custo_por_papel)
projeção     = custo_sessao × 384 clientes do piloto   (cabe_core.finops.projecao; só multiplicação, rótulo "projeção")
```

Sem `status: confirmada` para um modelo, o custo daquele papel fica `null` e o painel diz "preço a conferir"; não se inventa preço. Conversão para BRL só com câmbio com fonte (não há; tudo em USD).

### Medido

Modo `tools`, sem validador (27/09 00h53, `agent/evals/resultado-2026-09-27-verificacao.md`; baseline em `config/finops.yaml`):

| Sessão | Chamadas | Tokens entrada / saída | p50 / p95 | Custo |
|---|---:|---:|---:|---:|
| Bruno (Rolando) | 3 | 15.483 / 352 | 2,7 s / 4,0 s | US$ 0,0129 |
| Ana (Escorregão) | 3 | 14.618 / 248 | 2,7 s / 3,4 s | US$ 0,0119 |

Modo `gi` com validador, custo por caso separado por papel (27/09 02h40, `agent/evals/resultado-finops-2026-09-27.md`, 6 chamadas do agente + 5 do validador):

| O que | Valor |
|---|---|
| Chamada do agente | ~12 mil tokens de entrada (prompt da Gi + 22 exemplos + contexto), 130–260 de saída: **~US$ 0,0075**, p50 3,7 s |
| Chamada do validador | ~3,2 mil tokens de entrada: **~US$ 0,0026**, p50 2,4 s |
| Turno completo (agente + validador) | **US$ 0,0096 a 0,0105**; com 1 regeneração, US$ 0,0174 |
| Jornada da demo (pergunta do PIX + oferta = 2 turnos com modelo; confirmar e avançar mês em código) | **≈ US$ 0,021** por sessão (≈ US$ 0,028 com 1 regeneração); com o preço de 2027, ≈ US$ 0,04 |
| Projeção · 384 clientes do piloto × 1 conversa por ciclo | **≈ US$ 8 por ciclo de fatura** (≈ US$ 16 em 2027) |

Esses são os números do slide "a conta fecha": o custo de conversar é centavos de dólar por cliente; o juro que sai do rotativo é dezenas de reais (Bruno: R$ 219,91 evitados em três ciclos, `docs/01`).

Cloud Run na demo: 1 vCPU e 1 GiB com instância mínima ligada custam cerca de US$ 0,43 por dia ociosos ([Cloud Run pricing](https://cloud.google.com/run/pricing)); fora da demo, `min-instances=0` e o custo vai a zero.

## 3. Travas (o que impede a conta de subir)

| Trava | Onde |
|---|---|
| Nenhum número sai do LLM; cálculo em código | `cabe_core`, guardião em `agent/cabe_no_bolso/callbacks.py` |
| Acompanhamento mensal determinístico | `agent/cabe_core/acompanhar.py`; `acompanhamento_com_llm: false` |
| Teto de chamadas por turno (agente + validador + 1 regeneração) | `agent/cabe_no_bolso/runtime.py : _turno_gi` |
| Teto de chamadas ao modelo por sessão (12): acima, mensagem segura sem chamar o modelo, registrada no trace (`finops.teto_sessao`) e no painel | `agent/server/conversa.py : teto_chamadas_por_sessao, _resposta_teto` (valor em `config/finops.yaml`; `CHAMADAS_LLM_POR_SESSAO_MAX` sobrepõe) |
| Teto de 8 ferramentas por turno no `before_tool` (estado `temp:` do ADK; padrão `limit_tool_calls` trazido de guiwatanabe/iai-cabe-no-bolso) | `agent/cabe_no_bolso/callbacks.py : before_tool` |
| Thinking mínimo e instrução enxuta | `generate_content_config` (`PENSAMENTO=budget:0`), `instruction.md` |
| Sessão em memória, 1 instância, concorrência 40 | `deploy/deploy.sh` |
| Rate limit por IP, tamanho de corpo, timeout | `agent/server/limites.py` |
| BigQuery: views agregadas, 1 consulta por sessão, bytes cobrados limitados | `agent/cabe_core/dados.py` |
| Orçamento do grupo com alertas em 50 / 80 / 100% | Cloud Billing, `deploy/orcamento.sh` (exige papel de billing; não executado) |

## 4. Medição

- **Por sessão:** `GET /api/painel/{sessao_id}` devolve o bloco `finops`: `chamadas_llm`, `chamadas_validador`, tokens de entrada e saída (totais e do validador), `latencia_p50_ms`/`latencia_p95_ms`, `custo_estimado` = `custo_acumulado_sessao_usd`, `custo_agente_usd`, `custo_validador_usd`, `por_papel`, `custo_por_turno[]`, `preco_fonte`, `vigencia`, `preco_status`, `preco_entrada_por_milhao_usd`/`preco_saida_por_milhao_usd`, `projecao_piloto` (`rotulo: "projeção"`, `clientes: 384`, `custo_usd`), `teto_chamadas_por_sessao`, `chamadas_por_papel` (agente, validador, regeneração, insight: chamadas, tokens, latências, modelo, custo), `entradas_bloqueadas` (mensagens barradas pela regex de injeção antes do modelo, custo zero), `modelo`, `modelo_validador`, `moeda: "USD"`. Montado em `agent/server/finops.py : resumo_sessao` sobre `cabe_core.finops`.
- **Por turno:** cada resposta de `POST /api/mensagem` e `POST /api/consentimento` traz `turno` com `chamadas_llm`, `chamadas_validador`, tokens por papel, `latencia_ms`, `custo_usd`, `custo_agente_usd`, `custo_validador_usd`, `modelo`, veredito do validador, regenerações e mensagem segura (`agent/server/sessoes.py : registrar_turno` → `server/finops.py : completar_turno`). O runtime do agente calcula o mesmo por turno (`runtime._finops_turno`) e no state (`runtime.finops_de`).
- **Por serviço (Cloud Logging):** a cada turno o servidor escreve no stdout uma linha JSON por papel (`evento: "turno"`, `papel: agente | validador | codigo`, `sessao_id`, `ordem`, `modelo`, `chamadas`, `tokens_entrada`, `tokens_saida`, `latencia_ms`, `custo_usd`, `validador_aprovado`, `regeneracoes`, `mensagem_segura`, `modo`, `gatilho`, `acao`), que o Cloud Run entrega como `jsonPayload`. Sem PII e sem conteúdo do extrato: `sessao_id` é um token aleatório e `acao` é o nome do chip. `LOG_TURNOS=false` desliga. O `after_model` do agente também loga cada chamada (`evento: "modelo"`, com `papel`, `modelo`, tokens e `custo_usd`).
- **Métricas baseadas em log:** `deploy/metricas.sh` cria `logging/user/cabe_custo_usd` (distribuição; somar por dia = custo diário), `cabe_latencia_ms` (distribuição; alinhamento percentile 95 = latência p95), `cabe_mensagens_seguras`, `cabe_tokens`, `cabe_regeneracoes` e `cabe_validador_reprovou` (contadores), todas com rótulos `papel` e `modelo`. Consulta direta no Logs Explorer: `jsonPayload.evento="turno" AND resource.labels.service_name="cabe-no-bolso"`.
- **Nos evals:** `agent/evals/rodar.py` imprime, por caso, `custo US$ (ag + val)` e, no rodapé, o custo da rodada por papel; `--limite-chamadas` vale por agente.
- **Por agente (alvo):** `BigQueryAgentAnalyticsPlugin` do ADK grava eventos do agente num dataset `agent_logs`, o que permite custo por cliente, por cenário e por dia com SQL.

## 5. Como a banca vê

1. **Painel da banca → aba FinOps** (`demo/app.js : painelFinops`, dados só de `/api/painel`): modelo do agente e do validador; chamadas, tokens de entrada e saída por papel; latência p50/p95 por chamada; **custo desta sessão em USD** e por papel; o preço por milhão de tokens com a **fonte e a vigência**; a **projeção para 384 clientes do piloto** com o rótulo "projeção"; o teto de chamadas por sessão (quantas foram usadas); e a lista **custo por turno** (ação, papel, tokens, latência, regenerações, mensagem segura, US$). Cada valor de custo carrega `data-origem` (`finops.custo_por_papel:total`, `finops.projecao:custo_usd`, `config:finops.yaml:precos`). Sem LLM (modo `sem_llm` ou respostas gravadas) a tela mostra **US$ 0** com a mesma fonte de preço, nunca "DESCONHECIDO".
2. **Aba Validador:** um registro por turno com as checagens em código e o veredito; nas respostas gravadas (plano B), "validador: aprovado (gravado)".
3. **Aba "Como cheguei aqui":** cada chamada ao modelo aparece no trace com duração e `llm: true`; o teto por sessão aparece como `finops.teto_sessao`.
4. **Cloud Logging / Monitoring:** as linhas `evento: "turno"` e as métricas `cabe_*` (passo acima); custo diário e latência p95 no Metrics Explorer.
5. **Evals:** `agent/evals/resultado-finops-2026-09-27.md` (custo por caso e da rodada) e o baseline de `config/finops.yaml`.

## 6. Decisões de escala (o que muda de 384 para milhões)

| Escala | O que muda | Por quê |
|---|---|---|
| Piloto (384) | nada além do que está | ≈ US$ 8 por ciclo |
| ia.i inteiro (milhões) | context caching da instrução e dos exemplos (entrada cacheada a US$ 0,075 por milhão, 10% do preço); validador por amostragem estatística em vez de 100%, ou no `gemini-2.5-flash`; batch para o insight do card no fechamento da fatura; ficha do cliente pré-calculada na gold | o custo é dominado pelos ~12 mil tokens de entrada repetidos (prompt + exemplos); o validador é um quarto do turno |

## 7. Latência no palco e as duas alavancas

Medido pela API em 27/09 02h (modo `gi`, validador ligado): 6,8 a 8,2 s por turno de parede (agente ~4 s + validador ~2,5 s); 16,6 s quando o validador pede uma regeneração. Duas alavancas, sem tocar no código, documentadas em `deploy/deploy.sh` e `agent/.env.example`:

| Alavanca | Efeito | Custo |
|---|---|---|
| `MODELO_VALIDADOR=gemini-2.5-flash` | validador num modelo mais rápido; o agente continua no 3.8 Flash | validador cai de ~US$ 0,0026 para ~US$ 0,001 por chamada (preço confirmado) |
| `VALIDADOR_ATIVO=false` | 1 chamada por turno (~4 s); ficam as checagens em código + guardião; o painel mostra "validador desligado" em cada turno | turno cai para ~US$ 0,0075 |

## 8. Checklist do dia

1. `min-instances=1` só depois do deploy final; voltar a 0 depois do pitch (`gcloud run services update cabe-no-bolso --min-instances=0`).
2. Evals ao vivo com teto de chamadas por agente (`--limite-chamadas`); comparação de modelos uma vez.
3. `deploy/metricas.sh` uma vez depois do deploy (papel `logging.configWriter`); conferir no Metrics Explorer que `cabe_custo_usd` recebe pontos.
4. Conferir o consumo do orçamento no Cloud Billing antes do pitch e anotar no painel; `deploy/orcamento.sh --mostrar` imprime o comando do orçamento com alertas 50/80/100% (executar exige papel na conta de faturamento).
5. Não rodar Antigravity nem Gemini CLI em loop.
