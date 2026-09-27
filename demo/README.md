# demo/ · demo web (Protótipo do Time 05)

A banca abre pelo QR no celular e percorre a jornada em menos de 3 minutos: fatura fechada → consentimento → insight no cartão → pagar abaixo do total → o ia.i entra antes da confirmação → diagnóstico → saídas lado a lado com custo → confirmação (nada é contratado) → avançar mês ×3 → painel da banca ("como cheguei aqui", júri, FinOps).

## Arquivos

- `index.html`, `app.css`, `app.js`: uma página, mobile-first (≈390px), sem framework, sem build. Fontes do Google com fallback de sistema. Tokens, componentes e copy de `docs/06-design-system-ai.md`. Modo escuro automático.
- `mock/ana.json`, `mock/bruno.json`, `mock/saude.json`: respostas gravadas (plano B). Geradas por `analise/gera_mock_demo.py` **a partir do `cabe_core` real** (`capacidade.motor`, `ofertas.montar`, `ofertas.plano_de`, `acompanhar.ciclo`, `painel.juri`) sobre `data/extrato_sintetico.csv.gz` e `config/taxas.yaml`. Cada número tem origem; tudo marcado `simulado: true`.

## Rodar

- Com a API (produção e dev): o FastAPI serve `demo/` em `/` e a API em `/api/...`. A demo detecta a API por `GET /api/saude`.
- Só a demo, com respostas gravadas (plano B): da raiz, `python3 -m http.server 8765 --directory demo` e abrir `http://localhost:8765/?mock=1`. Sem `?mock=1`, a demo tenta `/api/saude` (2,5 s) e cai no mock sozinha se falhar. `?persona=ana|bruno` escolhe a persona inicial.
- Regerar os mocks depois de mudar o núcleo ou o `config/taxas.yaml`: `python3 analise/gera_mock_demo.py` (pandas e pyyaml; sem rede, sem LLM).
- Conferir sintaxe: `node --check demo/app.js`.

## Personas (clientes reais da base; apelidos fictícios)

| Persona | Cliente | Mês | Caminho |
|---|---|---|---|
| Ana · Escorregão | `755627ab-…` | 202508 | falta pontual → cobertura curta pelo limite da conta (7 dias, R$ 7,98) |
| Bruno · Rolando | `3e7d20b2-…` | 202509 | falta que se repete → parcelamento (consignado 10× R$ 110,51, custo R$ 186,02) |

## O que a interface impõe

- Nenhum número é calculado no navegador. Todo número exibido carrega `data-origem`, resolvida a partir de `numeros_validados` da API (o mesmo conjunto que o guardião usa). Número sem origem aparece sublinhado em vermelho.
- A expressão de condição vaga que a spec proíbe nunca aparece: a interface a substitui por "depende de aprovação". A IA se identifica como IA e oferece uma pessoa em todo fluxo.
- Sem adesão: só as formas de pagar, sem análise nem oferta. Recusa da oferta: a IA informa o custo uma vez e não insiste.
- Rodapé fixo em todas as telas: "Protótipo do Time 05. Data, taxas, elegibilidade e meses seguintes são simulados. Nenhum pagamento ou contratação real."
- Acessibilidade: contraste AA, 16px no corpo, botões de 44px, `aria-live` no chat, ordem de leitura da conversa.

## Contrato que a demo espera da API (extensões marcadas com †)

- `POST /api/sessao {cliente_id, anomes, persona}` → `{sessao_id, cliente: {apelido, perfil, grupo_rotulo}, anomes†, mes_rotulo†, fatura: {valor, vencimento_dia, minimo, opcoes_pagamento: [{rotulo, valor, acao†, valor_gravado†}]}, consentimento, numeros_validados}`.
- `POST /api/consentimento {sessao_id, concedido}` → `{consentimento, registro: {data, versao_texto, escopo}, insight†: {estado, texto, botao: {rotulo, acao}}, numeros_validados†}`. Sem `insight`, a demo mostra um convite genérico.
- `POST /api/mensagem {sessao_id, texto? | acao?, valor†?}` → `{mensagens, cards: [{tipo, dados}], numeros_validados, guardiao, sugestoes†: [{rotulo, acao, secundario?}]}`. Ações: `ver_opcoes | pagar_minimo | pagar_outro_valor | consigo_pagar | por_que_alta | confirmar | falar_com_pessoa | nao_quero`. `dados` dos cards são os dicts do `cabe_core`: `diagnostico` = `capacidade.motor`; `comparador` = `ofertas.montar`; `confirmacao` = `{resumo, plano (ofertas.plano_de), opcao, teto_cartao_mes, aviso}`; `insight` = `{texto, paga_agora, botao_primario, botao_secundario}`; `aviso` = `{texto}`; `encaminhamento` = `{motivo, texto, status}`.
- `POST /api/avancar-mes {sessao_id}` → `{anomes, fatura, paga_inteira, ciclos_ok, encerrado, cards: [{tipo: 'acompanhamento', dados: acompanhar.ciclo}], numeros_validados, mensagem†}`.
- `GET /api/trace/{sessao_id}` → `[{ordem, ferramenta, argumentos, resumo, numeros: [{valor, origem}], duracao_ms, ts, llm†}]`.
- `GET /api/painel/{sessao_id}` → `painel.juri` + `finops: {chamadas_llm, tokens_entrada, tokens_saida, latencia_p50_ms, latencia_p95_ms, custo_estimado}`.
- `GET /api/saude` → `{ok, dados, modelo}`. Erros: `{erro, mensagem_cliente}`.

Os mocks em `mock/` são o exemplo vivo de cada resposta.

## QR code

Gerar a partir da URL final do Cloud Run (`qrcode` em Python ou qualquer gerador); colocar no slide e em um cartão impresso na mesa. Se o Cloud Run falhar, a mesma demo roda localmente com `?mock=1`.
