# Alinhamento da camada gold (Lucas) com o motor `cabe_core` e o contrato `docs/10`

Conferido em 27/09 à 01h15 contra o BigQuery (`batalha-time-05-xew3.hackathon_dados`, 4 consultas, só as duas personas; resultado em cache em `personas-gold-2026-09-27.json`). Script: `analise/confere_gold_lucas.py` (rodar de novo não consulta o BigQuery; `--atualizar` refaz as 4 consultas).

O que vale para a demo hoje: o agente lê o extrato bruto (`extrato_sintetico` via `FonteBigQuery`, ou o CSV) e calcula em Python. A gold do Lucas é a **camada-alvo**: o motor passa a ler `gold_contexto_agente` quando as definições abaixo baterem. Nada disso bloqueia o pitch; é o que ajustar às 8h30 para a gold e o motor falarem a mesma língua.

## 1. Tabela por campo

| Campo | Lucas (`camada_analitica/sql`) | `cabe_core` (motor) | `docs/10` | Muda número? |
|---|---|---|---|---|
| Janela | `cash90_hackathon` = 03/10 a 31/12/2025 (últimos 90 dias da base, fixa) | 3 meses fechados antes do mês de referência + o mês (`calendario.janela`; Ana 202505–202508, Bruno 202506–202509) | idem motor | **Sim**: a gold só tem o "hoje" = dez/2025; as personas da demo (ago e set/2025) não têm linha |
| Fatura do mês | `fatura_estimada = compras_mes_anterior × 1,33` (`silver_cartao`, último mês) | reconstrução exata por modo: integral → pago; mínimo → pago ÷ 0,15; parcial → pago + juros ÷ 0,14 (`fatura.reconstruir`) | exata; 1,33 só projeta as próximas | **Sim** (Ana ago/25: R$ 2.955,00 exata × R$ 3.863,38 por 1,33; Bruno set/25: R$ 3.619,95 × R$ 3.189,14) |
| Renda | `renda_mensal_estimada = entradas_90d ÷ 3` (todo `tipo = E`: salário, PIX, 13º, PLR) | mediana mensal de `Salario CLT` + `Beneficio INSS`; PIX regular vira `entrada_regular_a_confirmar` e só soma depois do sim (`anomalia.renda`, `contar_pix`) | renda recorrente; PIX é flag | **Sim** (Ana: R$ 9.467,32 × R$ 5.467,13 + PIX R$ 2.177,92 a confirmar; Bruno: R$ 5.097,71 × R$ 3.787,42) |
| Dia do recebimento | `dia_recebimento_estimado` = mediana do dia de **todas** as entradas | moda do dia do salário/INSS | dia do salário ou benefício | Sim (Ana: 9 × 5/7) e muda `dias_ate_recebimento` |
| Fixos | `gasto_recorrente_mensal_estimado` = soma ÷ 3 de macro `LIKE casa/educa/emprést` + micro `LIKE condom/mensalidade/financiamento/assinatura` (inclui compras no cartão dessas categorias) | fixos = mediana mensal das 13 micros de `config/taxas.yaml` (`fixos.micros`), fora do cartão; essenciais = mediana das macros `essenciais.macros`, fora dos fixos e fora do cartão | mesmas listas | **Sim** (Ana dez/25: R$ 4.841,30 × R$ 4.690,59 + R$ 149,06; Bruno: R$ 1.539,22 × R$ 917,89 + R$ 639,51) |
| Parcelas em curso | `parcelas_futuras_estimadas` = Σ (parcela_total − parcela_atual) × vlr de **cada** lançamento parcelado nos 90 dias (o mesmo contrato entra uma vez por mês) | `anomalia.composicao_fatura.parcelas_em_curso` = quantidade e valor das compras parceladas do mês anterior (já dentro da fatura) | — | Sim (Bruno: R$ 3.333,07; conta em triplo) |
| Folga | `renda_mensal_estimada − gasto_recorrente_mensal_estimado` | `renda_recorrente − fixos − essenciais` | idem motor | **Sim** (consequência das três linhas acima: Ana R$ 4.626,02 × R$ 627,48 na mesma janela) |
| Saldo no vencimento | `saldo_atual` = último `saldo_apos` da cash90; `valor_faltante = fatura_estimada − saldo_atual` | não usa `saldo_apos` (`data/README`: só fecha em 26,8% das transições); falta = fatura − folga | falta = fatura − folga | **Sim** (Bruno: `saldo_atual` −R$ 6.473,41 → falta R$ 8.241,99 para uma fatura de R$ 1.768,58; o motor diz que dez/25 cabe) |
| Roladas / grupo | `qtd_pedaladas_12m` sobre o **ano inteiro** (jan–dez) em `silver_historico_fatura_12m` | roladas nos **12 meses anteriores** ao mês de referência + a rolada do gatilho (`grupo.classificar(modo_atual=...)`) | 12 meses anteriores; `anomes_ref` | Sim para o mês da demo (Bruno: 5 no ano × 4 anteriores a set + 1 do mês; Ana: 1 × 0 + 1). Grupo coincide nas duas personas, mas o ano inteiro olha o futuro |
| PONTUAL × ESTRUTURAL | `PONTUAL` se `folga_mensal_estimada ≥ valor_faltante`, senão `ESTRUTURAL` | pontual se o próximo recebimento cobre a falta em ≤ 25 dias (`cobertura_curta.teto_dias`), falta ≤ renda recorrente, renda não irregular e menos de 3 roladas; senão estrutural (`capacidade.motor`) | "faltam R$ X no dia Y e o dinheiro volta em Z dias" | **Sim** (Ana ago/25: pontual, 7 dias; a regra do Lucas daria ESTRUTURAL porque folga R$ 325,80 < falta R$ 2.629,20) |
| Elegibilidade | cobertura só `ESCORREGAO + PONTUAL`; parcelamento só `ROLANDO_FATURA + ESTRUTURAL`; `NO_LIMITE → FORA_MVP` | caminho pela capacidade, grupo como trava: pontual → cobertura (grupos `escorregao`, `em_dia`); estrutural → parcelamento (grupos `escorregao`, `rolando`); `no_limite` → sem crédito, formas de pagar + pessoa (item 2a do prompt da Gi); parcela ≤ folga, mais barato que o rotativo, liberado (`travas`) | idem motor | Sim no rótulo (`FORA_MVP`) e no Escorregão com falta estrutural (hoje `SEM_OFERTA_AUTOMATICA`, no motor vai a parcelamento) |
| Unidade | `FLOAT64` em reais | `INT64` em centavos (`round(vlr × 100)`) | centavos | Não muda número (a `FonteBigQuery` converte); só padroniza |
| Detecção de rolada | `descricao LIKE '%fatura%' OR '%fat%cart%'` + `parcial/minimo/mínimo` | `nom_cate_micro = 'Pagamento de fatura'` + `descr` contém `minimo` ou `parcial` | idem motor | **Não**: mesmas contagens na base (311/101/384/204 = os 689 abaixo do total de `docs/01`) |
| Compras no cartão | `descr LIKE '%cart credito%' AND NOT LIKE '%pag%fat%'` | `descr` começa com `cart credito` | idem | **Não** (equivalentes: o pagamento começa com `pag fat`) |
| Faixas de grupo | 0 / 1–2 / 3–5 / 6+ | `grupos` em `config/taxas.yaml`, mesmas faixas | idem | **Não** |

## 2. As duas personas, lado a lado (saída de `analise/confere_gold_lucas.py`, 27/09 01h15)

Três colunas: o que a gold diz (janela cash90 = out–dez/2025), o motor na **mesma** janela (mês de referência dez/2025, para isolar diferença de definição) e o motor no mês da demo.

### Ana · `755627ab` · demo em ago/2025

cash90_hackathon: 2025-10-03 a 2025-12-31 (anomes 202510–202512, 106 lançamentos). Motor na mesma janela: referência 202512, janela 202509–202512.

| Campo | Gold/Silver (Lucas, janela cash90) | Motor, mesma janela (ref 202512) | Motor, mês da demo (202508) |
|---|---|---|---|
| grupo | ESCORREGAO | escorregao | escorregao |
| faturas roladas contadas | 1 (ano inteiro, jan–dez) | 1 nos 12 anteriores (+1 do mês = 1) | 0 nos 12 anteriores (+1 do mês = 1); ano inteiro = 1 |
| última rolada | 2025-08 | — | — |
| fatura do mês | R$ 715,03 (1,33 × compras do mês anterior, último mês da cash90) | R$ 537,62 exata (modo integral); 1,33× daria R$ 715,03 | R$ 2.955,00 exata (modo minimo); 1,33× daria R$ 3.863,38 |
| silver_cartao 202508 | compras R$ 308,22; anterior R$ 2.904,80; fatura_estimada R$ 3.863,38 | — | — |
| silver_cartao 202512 | compras R$ 838,23; anterior R$ 537,62; fatura_estimada R$ 715,03 | — | — |
| renda mensal | R$ 9.467,32 (entradas_90d ÷ 3 = R$ 28.401,95 ÷ 3; inclui PIX, 13º, PLR) | R$ 5.467,13 mediana de salário/INSS; PIX mediana R$ 2.177,92 (regular: True); entradas ÷ meses = R$ 9.025,10 | R$ 5.467,13 mediana de salário/INSS; PIX mediana R$ 1.994,62 (regular: True); entradas ÷ meses = R$ 7.455,68 |
| dia do recebimento | 9 | 5 | 7 |
| compromissos do mês | fixos R$ 4.841,30 (macros casa/educação/empréstimo + micros condomínio/mensalidade/financiamento/assinatura ÷ 3); parcelas futuras R$ 131,66 | fixos R$ 4.690,59 + essenciais R$ 149,06 (medianas; lista de taxas.yaml; compras no cartão fora) | fixos R$ 4.669,84 + essenciais R$ 471,49 |
| folga do mês | R$ 4.626,02 | R$ 627,48 | R$ 325,80 |
| saldo no vencimento | saldo_atual R$ 21.896,88 (último saldo_apos da cash90) | não usa saldo_apos (data/README: não fecha) | idem |
| falta / cabe | R$ 0,00 (fatura_estimada − saldo_atual) · cabe: True | R$ 0,00 (fatura exata − folga) · cabe: True | R$ 2.629,20 · cabe: False |
| tipo da falta | SEM_FALTA (PONTUAL se folga ≥ falta) | nenhuma (pontual se o recebimento cobre em ≤ 25 dias: 5 dias) | pontual (7 dias até o recebimento) |
| elegibilidade / caminho | cobertura False · parcelamento False · FATURA_CABE → SEM_INTERVENCAO | nenhum → None (humano: False) | cobertura_curta → cheque_especial (humano: False) |

### Bruno · `3e7d20b2` · demo em set/2025

cash90_hackathon: 2025-10-05 a 2025-12-31 (anomes 202510–202512, 179 lançamentos). Motor na mesma janela: referência 202512, janela 202509–202512.

| Campo | Gold/Silver (Lucas, janela cash90) | Motor, mesma janela (ref 202512) | Motor, mês da demo (202509) |
|---|---|---|---|
| grupo | ROLANDO_FATURA | rolando | rolando |
| faturas roladas contadas | 5 (ano inteiro, jan–dez) | 5 nos 12 anteriores (+1 do mês = 5) | 4 nos 12 anteriores (+1 do mês = 5); ano inteiro = 5 |
| última rolada | 2025-09 | — | — |
| fatura do mês | R$ 1.768,58 (1,33 × compras do mês anterior, último mês da cash90) | R$ 1.986,66 exata (modo integral); 1,33× daria R$ 1.768,58 | R$ 3.619,95 exata (modo parcial); 1,33× daria R$ 3.189,14 |
| silver_cartao 202509 | compras R$ 2.094,10; anterior R$ 2.397,85; fatura_estimada R$ 3.189,14 | — | — |
| silver_cartao 202512 | compras R$ 1.831,34; anterior R$ 1.329,76; fatura_estimada R$ 1.768,58 | — | — |
| renda mensal | R$ 5.097,71 (entradas_90d ÷ 3 = R$ 15.293,12 ÷ 3; inclui PIX, 13º, PLR) | R$ 3.787,42 mediana de salário/INSS; PIX mediana R$ 143,44 (regular: False); entradas ÷ meses = R$ 4.770,14 | R$ 3.787,42 mediana de salário/INSS; PIX mediana R$ 141,45 (regular: False); entradas ÷ meses = R$ 3.888,54 |
| dia do recebimento | 7 | 5 | 7 |
| compromissos do mês | fixos R$ 1.539,22 (macros casa/educação/empréstimo + micros condomínio/mensalidade/financiamento/assinatura ÷ 3); parcelas futuras R$ 3.333,07 | fixos R$ 917,89 + essenciais R$ 639,51 (medianas; lista de taxas.yaml; compras no cartão fora) | fixos R$ 928,94 + essenciais R$ 157,61 |
| folga do mês | R$ 3.558,49 | R$ 2.230,02 | R$ 2.700,87 |
| saldo no vencimento | saldo_atual −R$ 6.473,41 (último saldo_apos da cash90) | não usa saldo_apos (data/README: não fecha) | idem |
| falta / cabe | R$ 8.241,99 (fatura_estimada − saldo_atual) · cabe: False | R$ 0,00 (fatura exata − folga) · cabe: True | R$ 919,08 · cabe: False |
| tipo da falta | ESTRUTURAL (PONTUAL se folga ≥ falta) | nenhuma (pontual se o recebimento cobre em ≤ 25 dias: 15 dias) | estrutural (17 dias até o recebimento) |
| elegibilidade / caminho | cobertura False · parcelamento True · AVALIAR_PARCELAMENTO → CONSULTAR_CREDITO | nenhum → None (humano: False) | parcelamento → consignado_clt (humano: False) |

Leitura: o grupo bate nas duas personas; tudo que depende de fatura, renda, folga e saldo diverge, e a divergência não é de dado (a mesma base) mas de definição. As cinco mudanças de SQL abaixo fecham a conta.

## 3. Mudanças de SQL sugeridas para as 8h30 (curtas; nomes atuais mantidos)

**(a) Mês de referência como parâmetro, janela móvel.** A demo simula datas (ago e set/2025). No topo de cada script:

```sql
DECLARE anomes_ref INT64 DEFAULT 202512;          -- mês da fatura em análise (a API passa o da sessão)
DECLARE anomes_ini INT64 DEFAULT CAST(FORMAT_DATE('%Y%m', DATE_SUB(PARSE_DATE('%Y%m', CAST(anomes_ref AS STRING)), INTERVAL 3 MONTH)) AS INT64);
-- e trocar `cash90_hackathon` por `extrato_sintetico WHERE anomes BETWEEN anomes_ini AND anomes_ref` nas silver de 90 dias
```

Alternativa mais simples: gerar as gold **por (id_usuario, anomes_ref)** para 202508–202512 e o agente filtra pelo mês da sessão (é o que `docs/10` chama de `anomes_ref`).

**(b) Fatura exata em vez de 1,33 × compras** (`silver_cartao` ou uma `silver_fatura` nova; o SQL completo já existe em `agent/sql/v_fatura.sql`):

```sql
-- por (id_usuario, anomes), a partir de extrato_sintetico
pago  = vlr do lançamento com nom_cate_micro = 'Pagamento de fatura'
modo  = CASE WHEN LOWER(descr) LIKE '%minimo%' THEN 'minimo' WHEN LOWER(descr) LIKE '%parcial%' THEN 'parcial' ELSE 'integral' END
juros = SUM(vlr) WHERE nom_cate_micro = 'Juros pagos' no mesmo (id_usuario, anomes)
fatura = ROUND(CASE modo WHEN 'integral' THEN pago WHEN 'minimo' THEN pago / 0.15 ELSE pago + juros / 0.14 END, 2)
dia_vencimento = EXTRACT(DAY FROM anomesdia) do pagamento
-- manter compras_mes_anterior * 1.33, renomeado para fatura_projetada (só para os meses seguintes)
```

**(c) Renda recorrente e PIX a confirmar** (`silver_recebimentos`):

```sql
-- por mês: rec = SUM(vlr) WHERE nom_cate_micro IN ('Salario CLT', 'Beneficio INSS'); pix = SUM(vlr) WHERE nom_cate_macro = 'Recebimentos diversos'
renda_recorrente   = mediana de rec entre os meses da janela            -- APPROX_QUANTILES(rec, 2)[OFFSET(1)]
dia_recebimento    = moda de EXTRACT(DAY FROM anomesdia) só dos lançamentos de salário/INSS
pix_mensal_mediana = mediana de pix; pix_regular = pix presente em todos os meses e |pix − mediana| / mediana <= 0.15
renda_irregular    = algum mês sem rec, ou |rec do mês / média dos anteriores − 1| > 0.15
-- entradas_90d / 3 pode ficar como renda_total_media (informativa); não entra na folga
```

**(d) Fixos, essenciais e folga sem `saldo_apos`** (`silver_compromissos` e `gold_capacidade_pagamento`):

```sql
fixos      = mediana mensal de vlr WHERE tipo = 'S' AND nom_cate_micro IN (<13 micros de config/taxas.yaml: fixos.micros>) AND LOWER(descr) NOT LIKE 'cart credito%'
essenciais = mediana mensal de vlr WHERE tipo = 'S' AND nom_cate_macro IN ('Mercado','Posto de combustivel','Transporte publico','Cuidados pessoais','Educacao','Pets','Casa')
             AND nom_cate_micro NOT IN (<fixos>) AND LOWER(descr) NOT LIKE 'cart credito%'
folga = renda_recorrente - fixos - essenciais
valor_faltante = GREATEST(fatura - folga, 0);  fatura_cabe = (valor_faltante = 0)
-- saldo_atual pode continuar na tabela como informação; não entra em valor_faltante (data/README.md: saldo_apos não fecha)
-- parcelas_futuras: contar cada contrato uma vez (última linha por descr + parcela_total) ou deixar para o motor
```

**(e) Roladas nos 12 meses anteriores e tipo da falta pelo recebimento** (`silver_historico_fatura_12m`, `gold_capacidade_pagamento`):

```sql
-- roladas: WHERE anomes < anomes_ref AND anomes >= (anomes_ref − 12 meses); rolou_no_mes_ref = MAX(flag_pedalada) WHERE anomes = anomes_ref
qtd_pedaladas_12m = roladas anteriores + rolou_no_mes_ref     -- a primeira rolada de quem estava em dia é Escorregão, não em dia
-- tipo da falta:
dias_ate_recebimento = IF(dia_recebimento > dia_vencimento, dia_recebimento - dia_vencimento, 30 - dia_vencimento + dia_recebimento)
tipo_falta = CASE WHEN valor_faltante = 0 THEN 'SEM_FALTA'
                  WHEN dias_ate_recebimento <= 25 AND valor_faltante <= renda_recorrente AND NOT renda_irregular AND roladas_anteriores < 3 THEN 'PONTUAL'
                  ELSE 'ESTRUTURAL' END
-- elegibilidade: 'FORA_MVP' -> 'SEM_CREDITO_AUTOMATICO' (No limite entra: formas de pagar + pessoa); ESCORREGAO + ESTRUTURAL -> AVALIAR_PARCELAMENTO
```

Depois disso, `gold_contexto_agente` (por `id_usuario, anomes_ref`) alimenta direto o `contexto_json` do modo Gi: `fatura.valor`, `capacidade.folga_mensal`, `capacidade.falta_prevista`, `capacidade.tipo_de_falta`, `faturas_abaixo_do_total_12m`, `entrada_regular_a_confirmar`. As ofertas (parcela, custo total, "cabe", ordem por custo) continuam em `cabe_core/ofertas.py` + `travas.py` com as taxas de `config/taxas.yaml`: a gold não precisa calcular oferta.

## 4. O que NÃO precisa mudar

- A arquitetura medallion e os nomes: `cash90_hackathon` como bronze D-90 ("cache" do app), silver por tema, três gold. `docs/10` mapeia: `silver_historico_fatura_12m` → `gold_perfil.roladas_12m`; `silver_cartao` (+ fatura exata) → `gold_fatura_mes`; `silver_recebimentos` + `silver_compromissos` → `gold_cliente_mes`; `gold_contexto_agente` → a ficha que o agente consome.
- Detecção de rolada (`%fatura%` / `%fat%cart%` + `parcial/minimo`) e de compra no cartão (`cart credito`, sem `pag fat`): equivalentes às do motor nesta base; a homologação (311 / 101 / 384 / 204) bate com `docs/01`.
- Faixas de grupo 0 / 1–2 / 3–5 / 6+ (as mesmas de `config/taxas.yaml`); `confianca_recebimento` (ALTA/MEDIA/BAIXA) é útil como flag ao lado de `renda_irregular`.
- O princípio "a LLM não calcula capacidade" e o fluxo gold → tools → agente.
- `silver_cliente_dia` (grade dia a dia) e `qtd_minimos_12m`: não são usados pelo motor, mas não atrapalham.
- Unidade em reais: a `FonteBigQuery` converte para centavos (`round(vlr × 100)`) na leitura; padronizar para `INT64` em centavos é desejável, não urgente.

## 5. Como conferir de novo

```bash
# da raiz; 4 consultas ao BigQuery só com --atualizar (senão usa o cache); --sem-bigquery imprime só o motor
CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares uv run --project agent --with google-cloud-bigquery python analise/confere_gold_lucas.py --atualizar
# alternativa com o bq do gcloud: acrescentar --via-bq
```

Critério de "bateu": para Ana em 202508 e Bruno em 202509, `gold_contexto_agente` (por `anomes_ref`) com `fatura`, `renda_recorrente`, `folga`, `valor_faltante`, `tipo_falta` e `qtd_pedaladas_12m` iguais à coluna "Motor, mês da demo" acima, com tolerância de 1 centavo (o `ROUND` do BigQuery arredonda 0,5 para longe do zero; o Python, para o par). Quando bater, `FonteBigQuery` passa a ler a gold em vez do extrato bruto, sem mudar a interface do motor.
