-- Resumo de compras no cartão por categoria e mês (D-90), usado pela tool
-- `explicar_fatura`. Compras que fazem parte de uma compra parcelada
-- (parcela_total > 1) são agrupadas na categoria sintética "Parcelas", em vez
-- da categoria original, pois já são contabilizadas em `parcelas_em_curso_c`.
CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_transacoes_resumo` AS
WITH compras AS (
  SELECT id_usuario, anomes, vlr, parcela_total,
    COALESCE(nom_cate_micro, 'Outros') AS categoria_original
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
  WHERE UPPER(tipo)='S'
    AND LOWER(COALESCE(descr,'')) LIKE '%cart credito%'
    AND LOWER(COALESCE(descr,'')) NOT LIKE '%pag%fat%'
),
classificadas AS (
  SELECT id_usuario, anomes, vlr,
    CASE WHEN parcela_total IS NOT NULL AND parcela_total > 1
      THEN 'Parcelas' ELSE categoria_original END AS categoria
  FROM compras
)
SELECT id_usuario, anomes, categoria,
  CAST(ROUND(SUM(vlr)*100) AS INT64) AS valor_c
FROM classificadas
GROUP BY id_usuario, anomes, categoria;
