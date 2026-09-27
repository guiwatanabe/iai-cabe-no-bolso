CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_cliente_dia` AS
WITH limites AS (
  SELECT MIN(DATE(anomesdia)) AS dt_min, MAX(DATE(anomesdia)) AS dt_max
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
),
calendario AS (
  SELECT data FROM limites, UNNEST(GENERATE_DATE_ARRAY(dt_min, dt_max)) AS data
),
clientes AS (
  SELECT DISTINCT id_usuario
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
),
grade AS (
  SELECT c.id_usuario, d.data FROM clientes c CROSS JOIN calendario d
),
movimentacao AS (
  SELECT id_usuario, DATE(anomesdia) AS data,
    SUM(CASE WHEN UPPER(tipo)='E' THEN vlr ELSE 0 END) AS entrada_dia,
    SUM(CASE WHEN UPPER(tipo)='S' THEN vlr ELSE 0 END) AS saida_dia,
    COUNT(*) AS qtd_transacoes
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
  GROUP BY id_usuario, DATE(anomesdia)
)
SELECT g.id_usuario, g.data,
  COALESCE(m.entrada_dia,0) AS entrada_dia,
  COALESCE(m.saida_dia,0) AS saida_dia,
  COALESCE(m.entrada_dia,0)-COALESCE(m.saida_dia,0) AS fluxo_liquido_dia,
  COALESCE(m.qtd_transacoes,0) AS qtd_transacoes
FROM grade g
LEFT JOIN movimentacao m USING (id_usuario, data);
