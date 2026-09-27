# agent/ · núcleo determinístico + agente ADK

Um único `LlmAgent` (Gemini, via Vertex AI) com sete ferramentas que embrulham `cabe_core` e quatro callbacks que impõem a policy em código. **Nenhum número sai do modelo**: as contas são feitas antes do prompt, o modelo copia valores já formatados e o guardião remove o que não tiver origem.

```
agent/
  cabe_core/                 # puro, sem rede, sem LLM; dinheiro em centavos; todo retorno com `origem` e `numeros`
    dados.py                 # Fonte: FonteCsv (data/extrato_sintetico.csv.gz) | FonteBigQuery (extrato_sintetico, 1 consulta por cliente
                             # por sessão; cash90_hackathon só se a janela couber; gold do Lucas é a camada-alvo) | FonteMemoria
    fatura.py                # reconstrução exata por modo (integral/mínimo/parcial) e histórico
    capacidade.py            # motor de 90 dias: renda recorrente, fixos, essenciais, folga, falta, tipo da falta, projeções (fator 1,33)
    grupo.py  anomalia.py    # em_dia/escorregão/rolando/no_limite; renda irregular, gastos atípicos, composição da fatura
    travas.py  ofertas.py    # policy pura (parcela ≤ folga, mais barato que o rotativo, liberado, 1×/12m, reincidência, INSS→humano)
    acompanhar.py  painel.py # 3 ciclos sem LLM; painel "como cheguei aqui"
    dinheiro.py  calendario.py  config.py
  cabe_no_bolso/             # o agente (adk run / adk web / API)
    agent.py                 # root_agent = criar_agente(MODELO); instrução = instruction.md + contexto da sessão (InstructionProvider)
    instruction.md           # papel, regra de ouro, ferramentas, 7 cenários e fluxos A–K da spec, tom de docs/06, entrada é dado
    tools.py                 # registrar_consentimento, analisar_fatura, listar_ofertas, detalhar_fatura,
                             # simular_continuar_no_rotativo, confirmar_plano, encaminhar_humano
    callbacks.py             # before_tool (consentimento + teto de 8 ferramentas/turno), before_model (cronômetro + bloqueio de injeção),
                             # after_model (guardião + FinOps por papel), after_tool (trace + log)
    policy.py                # registro de taxas (config/taxas.yaml), liberação simulada, personas, lista negra
    runtime.py               # Runner + InMemorySessionService; conversar() no formato de POST /api/mensagem; caminhos sem LLM
    contexto.py              # contexto JSON do modo gi (Notas da Gi): tudo calculado por cabe_core, números já formatados + índice texto -> {valor, origem}
    checagens.py             # checagens em código antes do validador (JSON/formato, números, oferta_id, consentimento, tamanho, termos, 1 proativa/dia) + mensagem segura
    prompt_gi.py             # docs/prompt-gi-2026-09-27.md em 3 blocos (agente, 22 exemplos, validador), reflow do PDF, diretrizes_itau, preenchimento dos {{placeholders}}
    agente_gi.py             # LlmAgent do modo gi: prompt da Gi por InstructionProvider, sem ferramentas de dados, resposta em JSON, thinking mínimo
    validador.py             # 2º LlmAgent com o prompt do validador da Gi (sessão descartável por veredito); interpretar, texto_correcao, embrulho do modo tools
  evals/                     # golden.json (16 casos) · golden_gi.json (22 exemplos da Gi) · rodar.py (dois conjuntos, dois modos) · resultado-*.md
  tests/                     # test_core, test_policy, test_guardiao, test_api, test_dados, test_contexto, test_checagens, test_validador_offline (sem modelo)
  sql/                       # DDL equivalente às definições do motor (não executado); a camada gold real é camada_analitica/ (Lucas)
```

## Rodar

```bash
cd agent && uv sync && cp .env.example .env        # Vertex + ADC: GOOGLE_GENAI_USE_VERTEXAI=TRUE, GOOGLE_CLOUD_LOCATION=global, MODELO
uv run pytest                                     # núcleo, policy, guardião, ferramentas, runtime, API e dados: sem LLM, ~2 s
uv run pytest tests/test_dados.py                 # FonteBigQuery com consulta injetada = mesmo número que o CSV; 1 consulta por cliente
uv run adk run cabe_no_bolso                      # agente no terminal (sessão sem cliente: informe id e mês na conversa)
uv run adk web                                    # Dev UI (AGENTS_DIR = agent/)
uv run python -m cabe_no_bolso.runtime 3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b 202509 ver_opcoes confirmar   # jornada pela API interna
uv run python evals/rodar.py --so-guardiao        # casos sem modelo (0 chamadas)
uv run python evals/rodar.py --conjunto gi        # 22 exemplos da Gi no modo gi, com validador (~24 chamadas do agente + ~24 do validador)
uv run python evals/rodar.py --conjunto golden --modo tools --limite-chamadas 25   # golden set no modo tools; --modelos a,b compara modelos
uv run python evals/rodar.py --sem-validador      # só agente + checagens/guardião
uv run python evals/rodar.py --gerar-evalset && uv run adk eval cabe_no_bolso evals/golden.evalset.json --config_file_path evals/test_config.json
```

Variáveis (`.env.example`): `MODELO` (padrão `gemini-3.8-flash`), `MODELO_RESERVA`, `MODELO_VALIDADOR` (padrão = `MODELO`), `MODO_CONVERSA` (`gi` = prompt da Gi com contexto pronto, **padrão** desde 27/09 porque os 22 exemplos da Gi passaram ao vivo, 22/22; `tools` = instrução + ferramentas; `sem_llm` = só o núcleo; o porquê no próprio `.env.example`), `VALIDADOR_ATIVO` (true), `INSIGHT_COM_LLM` (false), `ACOMPANHAMENTO_COM_LLM` (false), `PENSAMENTO` (`budget:0` por padrão: o `gemini-3.8-flash` no Vertex aceita `thinking_budget=0` e recusa `thinking_level=minimal` com 400), `TEMPERATURA` (0,2), `MAX_TOKENS_SAIDA` (2048, inclui raciocínio), `DADOS=csv|bigquery`, `BIGQUERY_TABELA`, `BIGQUERY_TABELA_90D` + `BIGQUERY_JANELA_90D` (`AAAAMM-AAAAMM`, opcional), `RAIZ`, `TAXAS`.

## Plataforma, identidade e FinOps

- **Identidade.** No Cloud Run o agente roda como `squad-agent-sa@batalha-time-05-xew3.iam.gserviceaccount.com` (`roles/aiplatform.user`, `bigquery.jobUser`, `logging.logWriter`; conferido em 27/09 01h) e chama o Gemini via Vertex AI com ADC. **Zero chaves JSON**: localmente, ADC do usuário ou impersonação sem chave (`gcloud auth application-default login --impersonate-service-account=squad-agent-sa@...`). A service account padrão do Compute não tem `aiplatform.user`. Quem é quem, papéis mínimos e ameaças × mitigação: `docs/12-identidade-e-seguranca.md`.
- **CI.** `deploy/cloudbuild.yaml` roda `uv run --frozen pytest -q` aqui em `agent/`, constrói a imagem do `Dockerfile` da raiz, publica em `agentes` e implanta com `--service-account=squad-agent-sa`; disparo manual (`gcloud builds submit --config deploy/cloudbuild.yaml ...`) até o repositório ser conectado ao Cloud Build. Caminho do dia e rollback: `deploy/deploy.sh`, `deploy/CHECKLIST.md`.
- **FinOps.** Preços com fonte, baseline medido (US$ 0,013 por sessão no modo tools sem validador) e travas em `config/finops.yaml` e `docs/11-finops.md`; o painel (`GET /api/painel/{sessao_id}.finops`) mostra chamadas, tokens, p50/p95 e `custo_estimado`.
- **Padrões do repositório do Guilherme** (`guiwatanabe/iai-cabe-no-bolso`, decisão de 27/09 01h30 em `docs/decisoes.md`): modelo `Gemini(model, client_kwargs={"location": "global"}, retry_options=HttpRetryOptions(attempts=3, max_delay=8))` em `agent.py`; `before_model` que bloqueia padrões de injeção e devolve uma `LlmResponse` de recusa, e `before_tool` com teto de 8 tool calls por turno (`state["temp:tool_calls"]`) em `callbacks.py`; `App(name, root_agent, plugins=[ReflectAndRetryToolPlugin, BigQueryAgentAnalyticsPlugin quando BQ_ANALYTICS_DATASET])`; BigQuery só com consulta parametrizada e `maximum_bytes_billed` (`cabe_core/dados.py`, `config/finops.yaml`).

## Como o agente funciona

1. **Sessão** (`runtime.criar_sessao`): estado com `cliente_id`, `anomes`, `consentimento=False`, `numeros_validados` (só a fatura do mês, do sistema de cartões), `trace`, `finops`.
2. **Consentimento** (`runtime.consentir`, sem LLM): registra data, versão do texto e escopo; com o sim, roda `capacidade.motor` e `ofertas.montar` pelo caminho determinístico e produz o insight do cartão. Sem o sim, `callbacks.before_tool` bloqueia `analisar_fatura`, `listar_ofertas` e `detalhar_fatura` e devolve ao modelo o pedido de permissão.
3. **Conversa** (`runtime.conversar`): a instrução leva o contexto da sessão com a análise e as saídas já calculadas (JSON compacto, valores formatados). O modelo conversa a partir delas; chama ferramenta só quando precisa (PIX como renda, custo do rotativo, confirmar, encaminhar). Pagar mínimo/outro valor, avançar mês e painel não passam pelo modelo.
4. **Guardião** (`callbacks.after_model`): extrai R$, %, contagens ("10 parcelas", "7 dias", "dia 20") do texto; confere contra `state["numeros_validados"]` (+ constantes de `config/taxas.yaml`) com tolerância de 1 centavo, aceitando `1.234,56`, `1234,56` e `1.234` (frase arredondada); frase com número sem origem é trocada por um pedido de conferência; "sujeito a" vira "depende de aprovação"; frases com seguro, prestamista, cashback, pontos, cartão novo, investimento, capitalização viram "isso não faz parte do plano"; frases com julgamento ("gasta demais", "deveria", "descontrole", "erro seu") somem. O runtime aplica a mesma função ao texto final juntado.
5. **Trace e FinOps** (`callbacks.after_tool`, `after_model`): cada ferramenta e cada chamada ao modelo entram em `state["trace"]` (ordem, argumentos sem id completo, resumo, números novos com origem, duração) e num log JSON no stdout sem conteúdo de extrato; `state["finops"]` acumula chamadas, tokens e latências (p50/p95 no painel) e, em `chamadas_por_papel`, separa agente, validador, regeneração e insight. `runtime.custo_estimado(finops)` devolve `{usd, preco_fonte, por_papel}` com o preço de `config/finops.yaml`; sem `status: confirmada`, `usd` fica `null` com o motivo.
6. **Dois modos, um validador** (`docs/notas-prompt-gi-2026-09-27.md`; resultado ao vivo em `evals/resultado-gi-2026-09-27.md`). `MODO_CONVERSA=tools` é o descrito acima. `MODO_CONVERSA=gi` monta o contexto JSON em código (`contexto.py`: modo `insight|conversa`, gatilho, consentimento, fatura, capacidade, ofertas liberadas, `entrada_regular_a_confirmar`, tudo já formatado como texto), entrega ao mesmo `LlmAgent` o prompt da Gi verbatim (`{{diretrizes_itau}}`, `{{contexto_json}}`, `{{exemplos}}` preenchidos pelo código, nunca texto do cliente no prompt de sistema) e exige resposta em JSON (`mensagens|texto, acao, oferta_id, numeros_citados`); sem ferramentas de dados. Nos dois modos: checagens em código (`checagens.py`) e depois o validador, um segundo `LlmAgent` com o prompt do validador da Gi (`MODELO_VALIDADOR`), que devolve `aprovado | violacoes | orientacao`; reprovado regenera uma vez; reprovado de novo ou R8 vira a mensagem segura. Toda reprovação vai para o trace (`ferramenta: validador`, com `aprovado`, `violacoes`, `orientacao`) e para o painel (`validador.reprovacoes`). Se o validador cair (rede, cota), a resposta que já passou nas checagens segue e o trace registra "validador indisponível". No modo gi as ações do JSON viram chamadas determinísticas a `tools.*` (`abrir_resumo_contrato` → `confirmar_plano`; `transferir_humano` → `encaminhar_humano`; `revogar_consentimento` → `registrar_consentimento(false)`) e cards (`mostrar_oferta` → diagnóstico + comparador; `mostrar_formas_de_pagar` → `opcoes_da_fatura`). Se o contexto trouxer `entrada_regular_a_confirmar` (PIX mensal), as diretrizes mandam perguntar antes de ofertar; a ação `confirmar_entrada_regular` (chip "Sim, é renda") faz o runtime refazer motor e ofertas com `contar_pix=True`, sem LLM. Acompanhamento continua sem LLM (`ACOMPANHAMENTO_COM_LLM=false`); o insight do cartão é determinístico por padrão (`INSIGHT_COM_LLM=false` liga o modo insight do prompt, com limite de 1 proativa por dia).

Interface para o servidor (assinaturas estáveis; todas com versão `*_async`):

```python
from cabe_no_bolso import runtime
runtime.criar_sessao(cliente_id, anomes, persona=None, sessao_id=None, simulacao=None) -> dict      # POST /api/sessao
runtime.consentir(sessao_id, concedido) -> dict                                                     # POST /api/consentimento
runtime.conversar(sessao_id, cliente_id, anomes, texto_ou_acao, valor=None, gatilho=None, modo=None, contar_pix=None) -> dict   # POST /api/mensagem
runtime.avancar_mes(sessao_id) -> dict                                                              # POST /api/avancar-mes (sem LLM)
runtime.trace(sessao_id) -> list; runtime.painel(sessao_id) -> dict; runtime.saude() -> dict       # GET /api/trace, /api/painel, /api/saude
runtime.insight_gi(sessao_id, gatilho="fechamento") -> {insight, saida, checagens, validador, nao_enviar, finops}   # modo insight (gi)
runtime.atualizar_estado(sessao_id, delta) -> dict                                                  # gancho do servidor: espelha decisões em código
runtime.gerar_gi(contexto_pronto, turnos) -> list[dict]                                             # evals: agente da Gi sobre um contexto ilustrativo
runtime.modo_conversa() -> "gi" | "tools"; runtime.configuracao() -> dict
```

`conversar` aceita as ações `ver_opcoes | consigo_pagar | por_que_alta | confirmar | falar_com_pessoa | nao_quero | pagar_minimo | pagar_outro_valor | confirmar_entrada_regular (contar_pix) | nao_contar_pix` ou texto livre, e devolve, nos dois modos, `{mensagens, cards, numeros_validados, guardiao, sugestoes, finops, acao, oferta_id, numeros_citados, validador: {ativo, aprovado, violacoes, orientacao, regeneracoes, mensagem_segura}, modo_conversa}` (+ `gatilho` no modo gi). `finops` traz `chamadas_llm`, `chamadas_validador`, `latencias_ms`, `latencias_validador_ms`, tokens e `ferramentas`. Erros vêm como `{erro, mensagem_cliente}`.

## Regras impostas em código (não por instrução)

| Regra | Onde |
|---|---|
| Consentimento antes de ler histórico | `callbacks.before_tool` |
| Nenhum número sem origem chega ao cliente | `callbacks.after_model` + `runtime.conversar` (segunda passada) |
| Só o que cabe, liberado e mais barato que o rotativo; grupo como trava; 1 parcelamento/12 meses; reincidência → humano; INSS → confirmação humana | `cabe_core/travas.py`, `cabe_core/ofertas.py` |
| Taxas só de `config/taxas.yaml`; sem taxa o produto some | `cabe_core/config.py`, `policy.registro_taxas` |
| O modelo não escolhe de quem lê nem inventa valor para simular | `tools._sessao` (estado vence o argumento), `tools.simular_continuar_no_rotativo` (só valores da sessão) |
| Sem produto fora do plano, sem "sujeito a", sem julgamento | `callbacks.guardiao_texto` |
| Sem LLM no acompanhamento e no painel | `runtime.avancar_mes`, `runtime.painel` |
| Uma consulta por cliente por sessão no BigQuery; mesmo número que o CSV | `cabe_core/dados.py : FonteBigQuery` (`test_dados.py`) |
| JSON válido, números citados no contexto, `oferta_id` liberada, sem oferta sem consentimento, tamanho, termos proibidos, 1 proativa por dia (nos dois modos) | `cabe_no_bolso/checagens.py : checar, mensagem_segura` (`tests/test_checagens.py`) |
| Nenhuma mensagem chega ao cliente sem passar pelo validador; regeneração única; mensagem segura; R8 → pessoa | `cabe_no_bolso/validador.py` (2º `LlmAgent`) + `runtime._turno_gi` / `_validar_modo_tools` (`tests/test_validador_offline.py`) |
| Texto do cliente nunca entra no prompt de sistema (o histórico vai pelos eventos da sessão; o validador recebe o prompt preenchido na mensagem de usuário) | `agente_gi.instrucao`, `validador.validar_async`, `contexto.montar` (`historico_conversa: []`) |

## Evals

`evals/golden.json`: 10 casos de `docs/05`, 4 da mentora (custo maior que o rotativo bloqueia; 2º parcelamento em 12 meses bloqueia; "sujeito a" reescrito; seguro/prestamista recusado com gentileza) e os cenários 3 e 6 da spec. Cada caso abre uma sessão nova sobre um cliente real do CSV, com consentimento, simulação de liberação/taxa e histórico de contratações no estado, e verifica: ferramentas (obrigatórias, proibidas, nenhuma leitura sem consentimento), números com origem, termos ausentes/presentes, cards e encaminhamento. `evals/rodar.py` imprime a tabela (aprovado/reprovado, fidelidade numérica, trajetória, termos barrados, latência por chamada, tokens) e o resumo por modelo. O resultado da última execução fica em `evals/resultado*.md`.

Última execução completa do golden set no modo tools (27/09 00h53, `gemini-3.8-flash` via Vertex `global`, `--limite-chamadas 25`): **16 de 16 aprovados**, 19 chamadas, 90.432 tokens de entrada / 1.978 de saída, latência por chamada p50 3,4 s / p95 5,6 s, fidelidade numérica 100% (`evals/resultado-2026-09-27-verificacao.md`; tabela copiada no README da raiz, §6).

`evals/golden_gi.json`: os 22 exemplos do prompt da Gi (6 insight + 16 conversa) com os contextos ilustrativos dos próprios exemplos, no formato do contexto das Notas; cada caso roda `runtime.gerar_gi` (contexto pronto → agente → checagens → validador → regenera 1x → mensagem segura) e confere ação, `oferta_id`, números citados, termos ausentes e tamanho. Execução ao vivo de 27/09 01h40 (`gemini-3.8-flash`, `PENSAMENTO=budget:0`, validador ligado): **22 de 22 aprovados** (19 na primeira passada; I3, I5 e C2 reprovaram por um erro no meu contexto ilustrativo do Bruno, parcela maior que a folga, e passaram depois da correção), 34 chamadas ao agente e 33 ao validador, latência do agente p50 3,3 s / p95 4,5 s (~8,5 mil tokens de entrada por chamada), validador p50 2,8 s / p95 3,9 s; 5 reprovações do validador seguidas de regeneração e aprovação (C2: R6). Modo tools com validador (3 casos): o embrulho do texto para o validador rotulava um turno de pergunta como oferta (R13); corrigido e confirmado com uma chamada. Detalhe, chamada a chamada, em `evals/resultado-gi-2026-09-27.md`. Orçamento da tarefa: 39 chamadas ao agente e 38 ao validador (teto 40 por agente).
