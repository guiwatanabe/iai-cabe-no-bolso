CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_recebimentos` AS
WITH entradas AS (
  SELECT id_usuario, DATE(anomesdia) AS data, vlr,
    EXTRACT(DAY FROM DATE(anomesdia)) AS dia_mes,
    nom_cate_micro
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
  WHERE UPPER(tipo)='E'
),
-- R11: dia_recebimento_estimado é a moda do dia de recebimentos de folha
-- (salário/benefício), não a mediana de todas as entradas (que inclui PIX etc.).
moda_folha AS (
  SELECT id_usuario, dia_mes, COUNT(*) AS qtd
  FROM entradas
  WHERE nom_cate_micro IN ('Salario CLT','Beneficio INSS')
  GROUP BY id_usuario, dia_mes
  QUALIFY ROW_NUMBER() OVER(PARTITION BY id_usuario ORDER BY qtd DESC, dia_mes ASC)=1
),
moda_geral AS (
  SELECT id_usuario, dia_mes, COUNT(*) AS qtd
  FROM entradas
  GROUP BY id_usuario, dia_mes
  QUALIFY ROW_NUMBER() OVER(PARTITION BY id_usuario ORDER BY qtd DESC, dia_mes ASC)=1
),
-- R17: público vulnerável = recebeu Benefício INSS na janela.
vulneravel AS (
  SELECT id_usuario, LOGICAL_OR(nom_cate_micro='Beneficio INSS') AS publico_vulneravel
  FROM entradas GROUP BY id_usuario
),
estatisticas AS (
  SELECT id_usuario,
    APPROX_QUANTILES(vlr,100)[OFFSET(50)] AS valor_recebimento_tipico,
    SUM(vlr) AS entradas_90d,
    COUNT(*) AS qtd_entradas_90d,
    COUNT(DISTINCT FORMAT_DATE('%Y-%m',data)) AS meses_com_entrada,
    MAX(data) AS ultimo_recebimento
  FROM entradas GROUP BY id_usuario
)
SELECT e.id_usuario, e.entradas_90d,
  e.entradas_90d/3.0 AS renda_mensal_estimada,
  e.valor_recebimento_tipico,
  -- fallback: cliente sem Salario CLT/Beneficio INSS na janela usa a moda geral.
  COALESCE(mf.dia_mes, mg.dia_mes) AS dia_recebimento_estimado,
  e.qtd_entradas_90d, e.meses_com_entrada, e.ultimo_recebimento,
  CASE WHEN e.meses_com_entrada=3 THEN 'ALTA'
       WHEN e.meses_com_entrada=2 THEN 'MEDIA'
       ELSE 'BAIXA' END AS confianca_recebimento,
  COALESCE(v.publico_vulneravel, FALSE) AS publico_vulneravel
FROM estatisticas e
LEFT JOIN moda_folha mf USING(id_usuario)
LEFT JOIN moda_geral mg USING(id_usuario)
LEFT JOIN vulneravel v USING(id_usuario);
