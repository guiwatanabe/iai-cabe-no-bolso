CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_compromissos` AS
WITH base AS (
  SELECT id_usuario, DATE(anomesdia) AS data,
    LOWER(COALESCE(nom_cate_macro,'')) AS categoria_macro,
    LOWER(COALESCE(nom_cate_micro,'')) AS categoria_micro,
    vlr, parcela_atual, parcela_total
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
  WHERE UPPER(tipo)='S'
),
flags AS (
  SELECT *,
    CASE WHEN categoria_macro LIKE '%casa%'
       OR categoria_macro LIKE '%educa%'
       OR categoria_macro LIKE '%emprest%'
       OR categoria_macro LIKE '%emprést%'
       OR categoria_micro LIKE '%condom%'
       OR categoria_micro LIKE '%mensalidade%'
       OR categoria_micro LIKE '%financiamento%'
       OR categoria_micro LIKE '%assinatura%'
      THEN 1 ELSE 0 END AS flag_recorrente,
    CASE WHEN parcela_total IS NOT NULL AND parcela_atual IS NOT NULL
       AND parcela_total > parcela_atual
      THEN (parcela_total-parcela_atual)*vlr ELSE 0 END AS compromisso_parcelado_futuro
  FROM base
)
SELECT id_usuario,
  SUM(CASE WHEN flag_recorrente=1 THEN vlr ELSE 0 END)/3.0 AS gasto_recorrente_mensal_estimado,
  SUM(compromisso_parcelado_futuro) AS parcelas_futuras_estimadas,
  SUM(CASE WHEN flag_recorrente=1 THEN vlr ELSE 0 END) AS compromissos_recorrentes_90d
FROM flags GROUP BY id_usuario;
