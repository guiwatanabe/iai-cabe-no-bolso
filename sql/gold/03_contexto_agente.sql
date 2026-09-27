CREATE OR REPLACE TABLE
  `batalha-time-05-xew3.hackathon_dados.gold_contexto_agente`
AS

SELECT
  /* Identificação técnica */
  id_usuario,
  data_simulada,

  /* Segmentação histórica */
  grupo_cliente,
  qtd_pedaladas_12m,

  /* Fatura */
  dia_vencimento,

  CAST(
    ROUND(fatura_estimada * 100)
    AS INT64
  ) AS fatura_estimada_c,

  /*
    Capacidade estimada do ciclo.
    Não representa saldo bancário real.
  */
  CAST(
    ROUND(caixa_disponivel_estimado * 100)
    AS INT64
  ) AS caixa_disponivel_estimado_c,

  /* Recebimentos */
  CAST(
    ROUND(renda_mensal_estimada * 100)
    AS INT64
  ) AS renda_mensal_estimada_c,

  CAST(
    ROUND(valor_recebimento_tipico * 100)
    AS INT64
  ) AS valor_recebimento_tipico_c,

  dia_recebimento_estimado,
  dias_ate_recebimento,
  confianca_recebimento,

  /* Capacidade mensal */
  CAST(
    ROUND(folga_mensal_estimada * 100)
    AS INT64
  ) AS folga_mensal_c,

  /*
    Quanto da própria fatura está sem cobertura.
    Este é o valor adequado para comunicação ao cliente.
  */
  CAST(
    ROUND(valor_faltante_fatura * 100)
    AS INT64
  ) AS valor_faltante_fatura_c,

  tipo_falta,

  /* Compromissos existentes */
  CAST(
    ROUND(parcelas_em_curso * 100)
    AS INT64
  ) AS parcelas_em_curso_c,

  /* Guardrails */
  publico_vulneravel,

  /* Elegibilidade analítica.
     Não significa crédito aprovado. */
  elegivel_cobertura_curta,
  elegivel_parcelamento

FROM
  `batalha-time-05-xew3.hackathon_dados.gold_elegibilidade`;
