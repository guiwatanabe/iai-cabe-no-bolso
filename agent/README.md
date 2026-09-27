# agent/ · especificação do agente (a construir)

Estrutura alvo (Python 3.11, `uv`, Google ADK):

```
agent/
  cabe_no_bolso/
    __init__.py
    agent.py          # root_agent = LlmAgent(...)
    instruction.md    # instrução do agente (tom e regras de docs/05 e docs/06)
    tools.py          # wrappers finos das funções de cabe_core como function tools
    callbacks.py      # before_tool (consentimento), after_model (guardião), after_tool (trace)
    policy.py         # elegibilidade, cabe no mês, C → humano, registro de taxas (config/taxas.yaml)
  cabe_core/
    __init__.py
    dados.py          # leitura: CSV local ou BigQuery (mesma interface)
    sinal.py  causa.py  urgencia.py  fatura.py  plano.py  acompanhar.py
    dinheiro.py       # centavos, arredondamento, formatação
  tests/
    test_core.py      # reproduz números de docs/01 para a persona 3e7d20b2
    test_policy.py    # golden set de docs/05
  server/
    main.py           # FastAPI + ADK Runner; serve demo/ estático
  .env.example
  pyproject.toml
```

## Contratos das ferramentas

Todas recebem `cliente_id: str` e `mes: int (AAAAMM)` quando aplicável e devolvem `dict` serializável com um campo `origem` por número.

- `detectar_sinal(cliente_id, mes, acao: Literal["pagar_minimo","pagar_parcial","simulou_parcelamento"], simulacoes: int = 0) -> dict`
- `diagnosticar_causa(cliente_id) -> dict`
- `classificar_urgencia(cliente_id) -> dict`
- `sobra_do_mes(cliente_id, mes, contar_pix: bool) -> dict`
- `montar_plano(cliente_id, mes, pagar_agora_centavos: int, contar_pix: bool) -> dict`
- `simular_rotativo(restante_centavos: int, meses: int) -> dict`
- `acompanhar(cliente_id, mes_seguinte) -> dict`
- `encaminhar_humano(cliente_id, motivo: str) -> dict`
- `registrar_consentimento(cliente_id, concedido: bool) -> dict`

## Regras que o código impõe (não a instrução)

1. Nenhuma ferramenta de dados roda sem `state["consentimento"] is True`.
2. `montar_plano` só devolve opções com `cabe == True`; se nenhuma cabe, devolve `encaminhar: True`.
3. Grupo C: `montar_plano` devolve só `encaminhar: True`.
4. Taxas vêm de `config/taxas.yaml`; ausência de taxa para um produto remove o produto.
5. O guardião (`after_model_callback`) remove qualquer número da resposta que não esteja em `state["numeros_validados"]` e bloqueia termos da lista negra (seguro, cashback, pontos, cartão novo, investimento).

## Instrução do agente (esqueleto)

Papel: "Você é o Cabe no Bolso, o agente do banco que ajuda a pessoa a sair da fatura rolada com um plano que cabe no mês dela." Regras: fale curto; um passo por vez; nunca invente número; se não souber, pergunte ou diga que não sabe; nunca julgue; nunca ofereça produto fora do plano; encaminhe para uma pessoa quando não couber, quando o cliente pedir ou quando houver angústia. Fluxo: sinal → consentimento → diagnóstico → pergunta certa → duas saídas com custo → confirmação → acompanhamento. Tom e copy: `docs/06-design-system-ai.md`.

## Rodar local (previsto)

```
uv sync
cp .env.example .env            # GOOGLE_API_KEY (dev) ou ADC; DADOS=csv
uv run pytest
uv run adk web                  # Dev UI; AGENTS_DIR = esta pasta (contém cabe_no_bolso/)
uv run adk run cabe_no_bolso    # agente no terminal
uv run uvicorn server.main:app  # API + demo
```
