# demo/ · demo web (Protótipo do Time 05)

A banca abre pelo QR no celular e percorre a jornada em menos de 3 minutos: fatura fechada → consentimento → insight no cartão → pagar abaixo do total → o ia.i entra antes da confirmação → diagnóstico → saídas lado a lado com custo → confirmação (nada é contratado) → avançar mês ×3 → painel da banca ("como cheguei aqui", júri, FinOps).

## Arquivos

- `index.html`, `app.css`, `app.js`: uma página, mobile-first (≈390px), sem framework, sem build. Fontes do Google com fallback de sistema. Tokens, componentes e copy de `docs/06-design-system-ai.md`. Modo escuro automático.
- `mock/ana.json`, `mock/bruno.json`, `mock/saude.json`: respostas gravadas (plano B). Geradas por `analise/gera_mock_demo.py` **a partir do `cabe_core` real** (`capacidade.motor`, `ofertas.montar`, `ofertas.plano_de`, `acompanhar.ciclo`, `painel.juri`) sobre `data/extrato_sintetico.csv.gz` e `config/taxas.yaml`. As mensagens seguem os exemplos do prompt da Gi (I2/I3, C1–C6) com os números do núcleo e trazem os campos do formato dela (`acao`, `oferta_id`, `numeros_citados`, `validador` gravado, `checagens`, `turno`). Cada número tem origem; tudo marcado `simulado: true` e `gravado: true`. `ana.json` traz a variante `com_pix` (contas refeitas depois de "É renda").

## Rodar

- Com a API (produção e dev): o FastAPI serve `demo/` em `/` e a API em `/api/...`. A demo detecta a API por `GET /api/saude`.
- Só a demo, com respostas gravadas (plano B): da raiz, `python3 -m http.server 8765 --directory demo` e abrir `http://localhost:8765/?mock=1`. Sem `?mock=1`, a demo tenta `/api/saude` (2,5 s) e cai no mock sozinha se falhar. `?persona=ana|bruno` escolhe a persona inicial.
- Regerar os mocks depois de mudar o núcleo, o `config/taxas.yaml` ou os textos: `cd agent && uv run python ../analise/gera_mock_demo.py` (o pacote `cabe_no_bolso` importa google-adk; sem rede, sem LLM).
- Conferir sintaxe: `node --check demo/app.js`.

## Personas (clientes reais da base; apelidos fictícios)

| Persona | Cliente | Mês | Caminho |
|---|---|---|---|
| Ana · Escorregão | `755627ab-…` | 202508 | falta pontual → cobertura curta pelo limite da conta (7 dias, R$ 7,98) |
| Bruno · Rolando | `3e7d20b2-…` | 202509 | falta que se repete → parcelamento (consignado 10× R$ 110,51, custo R$ 186,02) |

## O que a interface impõe

- Nenhum número é calculado no navegador. Todo número exibido carrega `data-origem`, resolvida a partir de `numeros_validados` da API (o mesmo conjunto que o guardião usa). Número sem origem aparece sublinhado em vermelho.
- A expressão de condição vaga que a spec proíbe nunca aparece: a interface a substitui por "depende de aprovação". A IA se identifica como IA e oferece uma pessoa em todo fluxo.
- Sem adesão: só as formas de pagar, sem análise nem oferta (o card do cartão mostra a mensagem segura: fatura, vencimento e "veja as formas de pagar"). Recusa da oferta: a IA informa o custo uma vez e não insiste.
- PIX que entra todo mês nunca é somado à renda por conta própria: a IA pergunta uma vez e a demo mostra os botões "É renda" (`confirmar_entrada_regular`: o servidor recalcula com `contar_pix=True`) e "Não é renda" (`nao_contar_pix`: segue sem contar).
- A frase "da mais barata para a mais cara" nunca aparece (prompt da Gi): o comparador só mantém a ordem por custo.
- Painel da banca, aba **FinOps**: custo desta sessão e por turno em USD (agente e validador separados), preço por milhão de tokens com fonte e vigência, latência p50/p95, projeção para 384 clientes do piloto (rótulo "projeção") e o teto de chamadas por sessão; cada valor de custo leva `data-origem`. Nas respostas gravadas, US$ 0.
- Painel da banca, aba **Validador**: por turno, as checagens em código (formato, números, oferta, consentimento, tamanho, termos proibidos) e o veredito do validador (aprovado/reprovado com as regras), regenerações e mensagens seguras, sob a frase "Nenhuma mensagem chega ao cliente sem passar pelo validador". Modelo: `nenhum (sem LLM)` quando a API roda sem modelo; `nenhum (mock)` nas respostas gravadas.
- Rodapé fixo em todas as telas: "Protótipo do Time 05. Data, taxas, elegibilidade e meses seguintes são simulados. Nenhum pagamento ou contratação real."
- Acessibilidade: contraste AA, 16px no corpo, botões de 44px, `aria-live` no chat, ordem de leitura da conversa.

## Contrato que a demo espera da API (extensões marcadas com †)

- `POST /api/sessao {cliente_id, anomes, persona}` → `{sessao_id, modo, modo_conversa (gi | tools | sem_llm), cliente: {apelido, perfil, grupo_rotulo}, anomes†, mes_rotulo†, fatura: {valor, vencimento_dia, minimo, opcoes_pagamento: [{rotulo, valor, acao†, valor_gravado†}]}, consentimento, insight (sem consentimento: mensagem segura, botao ver_formas_de_pagar), numeros_validados}`.
- `POST /api/consentimento {sessao_id, concedido}` → `{consentimento, registro: {data, versao_texto, escopo}, insight†: {estado, texto, botao: {rotulo, acao}, botao_secundario?, gerado_por: 'llm' | 'codigo', numeros_citados?, validador?}, turno?, numeros_validados†}`. No modo gi com `INSIGHT_COM_LLM=true` o texto vem do modelo (modo insight, gatilho fechamento) e passa pelas checagens e pelo validador; se falhar, texto seguro. Sem `insight`, a demo mostra um convite genérico.
- `POST /api/mensagem {sessao_id, texto? | acao?, valor†?}` → `{mensagens, cards: [{tipo, dados}], numeros_validados, guardiao, sugestoes†: [{rotulo, acao, secundario?}], acao (formato da Gi: nenhuma | mostrar_formas_de_pagar | mostrar_oferta | abrir_resumo_contrato | transferir_humano | devolver_ao_iai | revogar_consentimento | …), oferta_id, numeros_citados, validador: {aprovado, violacoes, regeneracoes, mensagem_segura}?, checagens, turno, gatilho, modo_conversa}`. Os campos novos são opcionais para a demo. Ações do cliente: `ver_opcoes | pagar_minimo | pagar_outro_valor | consigo_pagar | por_que_alta | confirmar | falar_com_pessoa | nao_quero | confirmar_entrada_regular | nao_contar_pix` (`contar_pix` é alias antigo). Mapeamento para o prompt da Gi: `ver_opcoes` → conversa/fechamento; `pagar_outro_valor` e `pagar_minimo` (valor = mínimo) → conversa/pagar_outro_valor; `consigo_pagar`, `por_que_alta` e texto livre → conversa/pergunta_cliente; `confirmar_entrada_regular` → recalcula com `contar_pix=True` e segue; `confirmar`, `falar_com_pessoa`, `nao_quero` → em código, sem modelo. `dados` dos cards são os dicts do `cabe_core`: `diagnostico` = `capacidade.motor`; `comparador` = `ofertas.montar`; `confirmacao` = `{resumo, plano (ofertas.plano_de), opcao, teto_cartao_mes, aviso}`; `insight` = `{texto, paga_agora, botao_primario, botao_secundario}`; `aviso` = `{texto}`; `encaminhamento` = `{motivo, texto, status}`; `formas_de_pagar` = `{texto, opcoes: [{rotulo, valor, acao}]}`; `opcoes_da_fatura` (runtime) = `{texto, formas: [{forma, valor}]}`.
- `POST /api/avancar-mes {sessao_id}` → `{anomes, fatura, paga_inteira, ciclos_ok, encerrado, cards: [{tipo: 'acompanhamento', dados: acompanhar.ciclo}], numeros_validados, mensagem†}`.
- `GET /api/trace/{sessao_id}` → `[{ordem, ferramenta, argumentos, resumo, numeros: [{valor, origem}], duracao_ms, ts, llm†}]`.
- `GET /api/painel/{sessao_id}` → `painel.juri` + `finops: {chamadas_llm, chamadas_validador, tokens_entrada, tokens_saida, tokens_entrada_validador, tokens_saida_validador, latencia_p50_ms, latencia_p95_ms, custo_estimado (= custo_acumulado_sessao_usd, USD), custo_agente_usd, custo_validador_usd, por_papel, custo_por_turno[], preco_fonte, vigencia, preco_status, preco_entrada_por_milhao_usd, preco_saida_por_milhao_usd, projecao_piloto: {rotulo: 'projeção', clientes: 384, custo_usd}, teto_chamadas_por_sessao, modelo, modelo_validador, moeda, rotulo_modelo}` (preço de `config/finops.yaml`; sem LLM ou no mock, US$ 0 com a mesma fonte) + `turnos: [{ordem, acao, modo, gatilho, llm, checagens: {formato, numeros, oferta, consentimento, tamanho, termos_proibidos, …}, validador: {aplicado, aprovado, violacoes, orientacao_para_regenerar}, regeneracoes, mensagem_segura, chamadas_llm, chamadas_validador, tokens_entrada, tokens_saida, latencia_ms, acao_agente, oferta_id, numeros_citados}]` + `validador: {ativo, modelo, frase, turnos_com_llm, aprovados, reprovados, regeneracoes, mensagens_seguras}` + `modo_conversa`, `rotulo_modelo`.
- `GET /api/saude` → `{ok, dados, modelo, modo, modo_conversa, rotulo_modelo, validador: {ativo, modelo, frase}}`. Erros: `{erro, mensagem_cliente}`.

Os mocks em `mock/` são o exemplo vivo de cada resposta.

## QR code

Gerar a partir da URL final do Cloud Run (`qrcode` em Python ou qualquer gerador); colocar no slide e em um cartão impresso na mesa. Se o Cloud Run falhar, a mesma demo roda localmente com `?mock=1`.
