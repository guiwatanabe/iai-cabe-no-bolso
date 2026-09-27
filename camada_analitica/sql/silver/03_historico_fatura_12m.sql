CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_historico_fatura_12m` AS
WITH eventos AS (
  SELECT id_usuario, DATE(anomesdia) AS data,
    LOWER(COALESCE(descr,'')) AS descricao, vlr
  FROM `batalha-time-05-xew3.hackathon_dados.extrato_sintetico`
),
flags AS (
  SELECT *,
    CASE WHEN (descricao LIKE '%fatura%' OR descricao LIKE '%fat%cart%')
      AND (descricao LIKE '%parcial%' OR descricao LIKE '%minimo%' OR descricao LIKE '%mínimo%')
      THEN 1 ELSE 0 END AS flag_pedalada,
    CASE WHEN (descricao LIKE '%fatura%' OR descricao LIKE '%fat%cart%')
      AND (descricao LIKE '%minimo%' OR descricao LIKE '%mínimo%')
      THEN 1 ELSE 0 END AS flag_minimo
  FROM eventos
),
mensal AS (
  SELECT id_usuario, FORMAT_DATE('%Y-%m',data) AS mes,
    MAX(flag_pedalada) AS pedalou_mes, MAX(flag_minimo) AS minimo_mes
  FROM flags GROUP BY id_usuario, FORMAT_DATE('%Y-%m',data)
),
cliente AS (
  SELECT id_usuario, SUM(pedalou_mes) AS qtd_pedaladas_12m,
    SUM(minimo_mes) AS qtd_minimos_12m,
    MAX(CASE WHEN pedalou_mes=1 THEN mes END) AS ultima_pedalada
  FROM mensal GROUP BY id_usuario
)
SELECT *,
  CASE WHEN qtd_pedaladas_12m=0 THEN 'SEMPRE_QUITA'
       WHEN qtd_pedaladas_12m BETWEEN 1 AND 2 THEN 'ESCORREGAO'
       WHEN qtd_pedaladas_12m BETWEEN 3 AND 5 THEN 'ROLANDO_FATURA'
       WHEN qtd_pedaladas_12m>=6 THEN 'NO_LIMITE' END AS grupo_cliente
FROM cliente;
