CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_recebimentos` AS
WITH entradas AS (
  SELECT id_usuario, DATE(anomesdia) AS data, vlr,
    EXTRACT(DAY FROM DATE(anomesdia)) AS dia_mes
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
  WHERE UPPER(tipo)='E'
),
estatisticas AS (
  SELECT id_usuario,
    APPROX_QUANTILES(vlr,100)[OFFSET(50)] AS valor_recebimento_tipico,
    CAST(ROUND(APPROX_QUANTILES(dia_mes,100)[OFFSET(50)]) AS INT64) AS dia_recebimento_estimado,
    SUM(vlr) AS entradas_90d,
    COUNT(*) AS qtd_entradas_90d,
    COUNT(DISTINCT FORMAT_DATE('%Y-%m',data)) AS meses_com_entrada,
    MAX(data) AS ultimo_recebimento
  FROM entradas GROUP BY id_usuario
)
SELECT id_usuario, entradas_90d,
  entradas_90d/3.0 AS renda_mensal_estimada,
  valor_recebimento_tipico, dia_recebimento_estimado,
  qtd_entradas_90d, meses_com_entrada, ultimo_recebimento,
  CASE WHEN meses_com_entrada=3 THEN 'ALTA'
       WHEN meses_com_entrada=2 THEN 'MEDIA'
       ELSE 'BAIXA' END AS confianca_recebimento
FROM estatisticas;
