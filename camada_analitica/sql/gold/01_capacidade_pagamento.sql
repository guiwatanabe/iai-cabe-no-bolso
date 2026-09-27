CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.gold_capacidade_pagamento` AS
WITH ultimo_saldo AS (
  SELECT id_usuario, saldo_apos AS saldo_atual
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
  QUALIFY ROW_NUMBER() OVER(PARTITION BY id_usuario ORDER BY anomesdia DESC)=1
),
base AS (
  SELECT f.*, s.saldo_atual,
    GREATEST(COALESCE(f.fatura_estimada,0)-COALESCE(s.saldo_atual,0),0) AS valor_faltante
  FROM `batalha-time-05-xew3.hackathon_dados.silver_cliente_features` f
  LEFT JOIN ultimo_saldo s USING(id_usuario)
)
SELECT *,
  CASE WHEN valor_faltante<=0 THEN TRUE ELSE FALSE END AS fatura_cabe,
  CASE WHEN valor_faltante<=0 THEN 'SEM_FALTA'
       WHEN valor_faltante>0 AND folga_mensal_estimada>=valor_faltante THEN 'PONTUAL'
       ELSE 'ESTRUTURAL' END AS tipo_falta
FROM base;
