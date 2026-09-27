# agent/ · núcleo determinístico + agente ADK

Um único `LlmAgent` (Gemini, via Vertex AI) com sete ferramentas que embrulham `cabe_core` e quatro callbacks que impõem a policy em código. **Nenhum número sai do modelo**: as contas são feitas antes do prompt, o modelo copia valores já formatados e o guardião remove o que não tiver origem.

```
agent/
  cabe_core/                 # puro, sem rede, sem LLM; dinheiro em centavos; todo retorno com `origem` e `numeros`
    dados.py                 # Fonte: FonteCsv (data/extrato_sintetico.csv.gz) | FonteBigQuery (agent/sql, esqueleto) | FonteMemoria
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
    callbacks.py             # before_tool (consentimento), after_model (guardião + FinOps), before_model, after_tool (trace + log)
    policy.py                # registro de taxas (config/taxas.yaml), liberação simulada, personas, lista negra
    runtime.py               # Runner + InMemorySessionService; conversar() no formato de POST /api/mensagem; caminhos sem LLM
  evals/                     # golden.json (16 casos), rodar.py (tabela markdown), golden.evalset.json + test_config.json (adk eval)
  tests/                     # test_core.py, test_policy.py, test_guardiao.py (tudo sem chamar o modelo)
  sql/                       # DDL das views gold no BigQuery (não executado)
```

## Rodar

```bash
cd agent && uv sync && cp .env.example .env        # Vertex + ADC: GOOGLE_GENAI_USE_VERTEXAI=TRUE, GOOGLE_CLOUD_LOCATION=global, MODELO
uv run pytest                                     # núcleo, policy, guardião, ferramentas e runtime: sem LLM, ~2 s
uv run adk run cabe_no_bolso                      # agente no terminal (sessão sem cliente: informe id e mês na conversa)
uv run adk web                                    # Dev UI (AGENTS_DIR = agent/)
uv run python -m cabe_no_bolso.runtime 3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b 202509 ver_opcoes confirmar   # jornada pela API interna
uv run python evals/rodar.py --so-guardiao        # casos sem modelo (0 chamadas)
uv run python evals/rodar.py --limite-chamadas 25 # golden set ao vivo (MODELO do .env); --modelos a,b compara modelos
uv run python evals/rodar.py --gerar-evalset && uv run adk eval cabe_no_bolso evals/golden.evalset.json --config_file_path evals/test_config.json
```

Variáveis (`.env.example`): `MODELO` (padrão `gemini-3.8-flash`), `MODELO_RESERVA`, `PENSAMENTO` (`low` por padrão no Gemini 3.x; `budget:256` no 2.5), `TEMPERATURA` (0,2), `MAX_TOKENS_SAIDA` (2048, inclui raciocínio), `DADOS=csv|bigquery`, `RAIZ`, `TAXAS`.

## Como o agente funciona

1. **Sessão** (`runtime.criar_sessao`): estado com `cliente_id`, `anomes`, `consentimento=False`, `numeros_validados` (só a fatura do mês, do sistema de cartões), `trace`, `finops`.
2. **Consentimento** (`runtime.consentir`, sem LLM): registra data, versão do texto e escopo; com o sim, roda `capacidade.motor` e `ofertas.montar` pelo caminho determinístico e produz o insight do cartão. Sem o sim, `callbacks.before_tool` bloqueia `analisar_fatura`, `listar_ofertas` e `detalhar_fatura` e devolve ao modelo o pedido de permissão.
3. **Conversa** (`runtime.conversar`): a instrução leva o contexto da sessão com a análise e as saídas já calculadas (JSON compacto, valores formatados). O modelo conversa a partir delas; chama ferramenta só quando precisa (PIX como renda, custo do rotativo, confirmar, encaminhar). Pagar mínimo/outro valor, avançar mês e painel não passam pelo modelo.
4. **Guardião** (`callbacks.after_model`): extrai R$, %, contagens ("10 parcelas", "7 dias", "dia 20") do texto; confere contra `state["numeros_validados"]` (+ constantes de `config/taxas.yaml`) com tolerância de 1 centavo, aceitando `1.234,56`, `1234,56` e `1.234` (frase arredondada); frase com número sem origem é trocada por um pedido de conferência; "sujeito a" vira "depende de aprovação"; frases com seguro, prestamista, cashback, pontos, cartão novo, investimento, capitalização viram "isso não faz parte do plano"; frases com julgamento ("gasta demais", "deveria", "descontrole", "erro seu") somem. O runtime aplica a mesma função ao texto final juntado.
5. **Trace e FinOps** (`callbacks.after_tool`, `after_model`): cada ferramenta e cada chamada ao modelo entram em `state["trace"]` (ordem, argumentos sem id completo, resumo, números novos com origem, duração) e num log JSON no stdout sem conteúdo de extrato; `state["finops"]` acumula chamadas, tokens e latências (p50/p95 no painel). `custo_estimado` fica `null` até haver preço com fonte em `config/taxas.yaml`.

Interface para o servidor (assinaturas estáveis; todas com versão `*_async`):

```python
from cabe_no_bolso import runtime
runtime.criar_sessao(cliente_id, anomes, persona=None, sessao_id=None, simulacao=None) -> dict      # POST /api/sessao
runtime.consentir(sessao_id, concedido) -> dict                                                     # POST /api/consentimento
runtime.conversar(sessao_id, cliente_id, anomes, texto_ou_acao, valor=None) -> dict                 # POST /api/mensagem
runtime.avancar_mes(sessao_id) -> dict                                                              # POST /api/avancar-mes (sem LLM)
runtime.trace(sessao_id) -> list; runtime.painel(sessao_id) -> dict; runtime.saude() -> dict       # GET /api/trace, /api/painel, /api/saude
```

`conversar` aceita as ações `ver_opcoes | consigo_pagar | por_que_alta | confirmar | falar_com_pessoa | nao_quero | pagar_minimo | pagar_outro_valor` ou texto livre, e devolve `{mensagens, cards, numeros_validados, guardiao, sugestoes, finops}`. Erros vêm como `{erro, mensagem_cliente}`.

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

## Evals

`evals/golden.json`: 10 casos de `docs/05`, 4 da mentora (custo maior que o rotativo bloqueia; 2º parcelamento em 12 meses bloqueia; "sujeito a" reescrito; seguro/prestamista recusado com gentileza) e os cenários 3 e 6 da spec. Cada caso abre uma sessão nova sobre um cliente real do CSV, com consentimento, simulação de liberação/taxa e histórico de contratações no estado, e verifica: ferramentas (obrigatórias, proibidas, nenhuma leitura sem consentimento), números com origem, termos ausentes/presentes, cards e encaminhamento. `evals/rodar.py` imprime a tabela (aprovado/reprovado, fidelidade numérica, trajetória, termos barrados, latência por chamada, tokens) e o resumo por modelo. O resultado da última execução fica em `evals/resultado*.md`.

Última execução completa (27/09 00h53, `gemini-3.8-flash` via Vertex `global`, `--limite-chamadas 25`): **16 de 16 aprovados**, 19 chamadas, 90.432 tokens de entrada / 1.978 de saída, latência por chamada p50 3,4 s / p95 5,6 s, fidelidade numérica 100% (`evals/resultado-2026-09-27-verificacao.md`; tabela copiada no README da raiz, §6).
