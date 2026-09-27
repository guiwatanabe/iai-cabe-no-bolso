CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_cartao` AS
WITH compras AS (
  SELECT id_usuario, anomes, SUM(vlr) AS compras_cartao_mes
  FROM `batalha-time-05-xew3.hackathon_dados.extrato_sintetico`
  WHERE UPPER(tipo)='S'
    AND LOWER(COALESCE(descr,'')) LIKE '%cart credito%'
    AND LOWER(COALESCE(descr,'')) NOT LIKE '%pag%fat%'
  GROUP BY id_usuario, anomes
),
historico AS (
  SELECT *,
    LAG(compras_cartao_mes) OVER(PARTITION BY id_usuario ORDER BY anomes) AS compras_mes_anterior
  FROM compras
)
SELECT id_usuario, anomes, compras_cartao_mes, compras_mes_anterior,
  compras_mes_anterior*1.33 AS fatura_estimada
FROM historico;
