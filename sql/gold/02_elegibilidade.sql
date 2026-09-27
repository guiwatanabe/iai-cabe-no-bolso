-- A mudança principal é que cobertura curta agora respeita explicitamente a regra dos 25 dias e verifica se o recebimento típico cobre a falta da fatura. 
-- Para parcelamento, a Gold identifica o candidato; o valor real da parcela será validado posteriormente contra a folga mensal.
CREATE OR REPLACE TABLE
  `batalha-time-05-xew3.hackathon_dados.gold_elegibilidade`
AS

SELECT
  *,

  /* Público inicial do Cabe no Bolso */
  CASE
    WHEN grupo_cliente IN (
      'ESCORREGAO',
      'ROLANDO_FATURA'
    )
    THEN TRUE
    ELSE FALSE
  END AS publico_mvp,

  /*
    COBERTURA CURTA

    Apenas:
    - Escorregão
    - falta pontual
    - existe valor da fatura sem cobertura
    - próximo recebimento em até 25 dias
    - recebimento esperado cobre a falta

    Aprovação de crédito NÃO acontece aqui.
  */
  CASE
    WHEN grupo_cliente = 'ESCORREGAO'
     AND tipo_falta = 'PONTUAL'
     AND valor_faltante_fatura > 0
     AND dias_ate_recebimento BETWEEN 1 AND 25
     AND valor_recebimento_tipico >= valor_faltante_fatura
    THEN TRUE
    ELSE FALSE
  END AS elegivel_cobertura_curta,

  /*
    PARCELAMENTO

    Apenas:
    - Rolando a Fatura
    - falta recorrente
    - existe capacidade mensal positiva

    A parcela efetiva ainda deverá respeitar:
    parcela <= folga_mensal_estimada.

    Produto e crédito são definidos fora do SQL.
  */
  CASE
    WHEN grupo_cliente = 'ROLANDO_FATURA'
     AND tipo_falta = 'RECORRENTE'
     AND valor_faltante_fatura > 0
     AND folga_mensal_estimada > 0
    THEN TRUE
    ELSE FALSE
  END AS elegivel_parcelamento,

  /*
    Diagnóstico analítico.
    Não representa aprovação de crédito.
  */
  CASE
    WHEN grupo_cliente = 'NO_LIMITE'
      THEN 'FORA_MVP'

    WHEN fatura_cabe = TRUE
      THEN 'FATURA_CABE'

    WHEN grupo_cliente = 'ESCORREGAO'
     AND tipo_falta = 'PONTUAL'
      THEN 'AVALIAR_COBERTURA_CURTA'

    WHEN grupo_cliente = 'ROLANDO_FATURA'
     AND tipo_falta = 'RECORRENTE'
      THEN 'AVALIAR_PARCELAMENTO'

    WHEN grupo_cliente IN (
      'ESCORREGAO',
      'ROLANDO_FATURA'
    )
      THEN 'SEM_OFERTA_AUTOMATICA'

    ELSE 'SEM_ACAO'
  END AS acao_motor

FROM
  `batalha-time-05-xew3.hackathon_dados.gold_capacidade_pagamento`;
