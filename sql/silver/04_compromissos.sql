CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_compromissos` AS
WITH base AS (
  SELECT id_usuario, anomes,
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
      THEN 1 ELSE 0 END AS flag_parcela_em_curso
  FROM base
),
recorrentes AS (
  SELECT id_usuario,
    SUM(CASE WHEN flag_recorrente=1 THEN vlr ELSE 0 END) AS compromissos_recorrentes_90d
  FROM flags GROUP BY id_usuario
),
-- R5: parcelas só do último mês disponível na janela, para não somar a mesma
-- parcela em curso uma vez por mês em que ela aparece (3/12, 4/12, 5/12, ...).
ultimo_mes AS (
  SELECT id_usuario, MAX(anomes) AS anomes_ultimo FROM flags GROUP BY id_usuario
),
parcelas_ultimo_mes AS (
  SELECT f.id_usuario,
    SUM(CASE WHEN f.flag_parcela_em_curso=1 THEN (f.parcela_total-f.parcela_atual)*f.vlr ELSE 0 END) AS parcelas_futuras_estimadas,
    SUM(CASE WHEN f.flag_parcela_em_curso=1 THEN f.vlr ELSE 0 END) AS parcelas_em_curso
  FROM flags f
  JOIN ultimo_mes u ON u.id_usuario=f.id_usuario AND f.anomes=u.anomes_ultimo
  GROUP BY f.id_usuario
)
SELECT r.id_usuario,
  r.compromissos_recorrentes_90d/3.0 AS gasto_recorrente_mensal_estimado,
  r.compromissos_recorrentes_90d,
  COALESCE(p.parcelas_futuras_estimadas,0) AS parcelas_futuras_estimadas,
  COALESCE(p.parcelas_em_curso,0) AS parcelas_em_curso
FROM recorrentes r
LEFT JOIN parcelas_ultimo_mes p USING(id_usuario);
