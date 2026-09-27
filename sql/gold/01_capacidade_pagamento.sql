/*
HOMOLOGAMOS:
- saldo_previsto substituído por caixa_disponivel_estimado.
- valor_faltante separado em falta da fatura e déficits do ciclo.
- fatura_cabe passa a usar valor_faltante_fatura.
- Mantidos tipo_falta e dias_ate_recebimento para o motor de ofertas.
*/

CREATE OR REPLACE TABLE
  `batalha-time-05-xew3.hackathon_dados.gold_capacidade_pagamento`
AS

SELECT
  f.*,

  /* Contexto do ciclo atual */
  cf.dia_vencimento,
  cf.data_simulada,
  cf.fatura_estimada,

  /* Capacidade financeira projetada */
  cf.caixa_disponivel_estimado,

  /* Separação dos déficits */
  cf.deficit_pre_fatura,
  cf.valor_faltante_fatura,
  cf.deficit_total_ciclo,

  /* Diagnóstico temporal */
  cf.tipo_falta,

  /*
    R16:
    dias entre vencimento da fatura
    e próximo recebimento esperado.
  */
  MOD(
    f.dia_recebimento_estimado
    - cf.dia_vencimento
    + 30,
    30
  ) AS dias_ate_recebimento,

  /*
    A fatura cabe quando não existe
    valor da própria fatura sem cobertura.
  */
  CASE
    WHEN cf.valor_faltante_fatura <= 0
      THEN TRUE
    ELSE FALSE
  END AS fatura_cabe

FROM
  `batalha-time-05-xew3.hackathon_dados.silver_cliente_features` f

JOIN
  `batalha-time-05-xew3.hackathon_dados.silver_ciclo_fatura` cf
USING (id_usuario);
