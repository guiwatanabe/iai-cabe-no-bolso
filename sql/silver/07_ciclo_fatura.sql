-- R3, R18, R19: projeção de caixa por ciclo de fatura, substituindo o
-- snapshot de `saldo_apos` (ver docs/regras-negocio.md "Projeção de caixa por
-- ciclo" para a simplificação adotada em relação ao plano original).
CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_ciclo_fatura` AS
WITH janela AS (
  SELECT MIN(DATE(anomesdia)) AS inicio_janela, MAX(DATE(anomesdia)) AS fim_janela
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
),
clientes AS (
  SELECT DISTINCT id_usuario FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
),
faturas AS (
  SELECT id_usuario, DATE(anomesdia) AS data_pagto,
    EXTRACT(DAY FROM DATE(anomesdia)) AS dia_mes
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
  WHERE nom_cate_micro = 'Pagamento de fatura'
),
saidas_todas AS (
  SELECT id_usuario, EXTRACT(DAY FROM DATE(anomesdia)) AS dia_mes
  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
  WHERE UPPER(tipo) = 'S'
),
-- R18: dia_vencimento = moda do dia do pagamento de fatura; sem pagamento na
-- janela, usa a moda de todas as saídas como aproximação.
moda_vencimento AS (
  SELECT id_usuario, dia_mes, COUNT(*) AS qtd
  FROM faturas GROUP BY id_usuario, dia_mes
  QUALIFY ROW_NUMBER() OVER(PARTITION BY id_usuario ORDER BY qtd DESC, dia_mes ASC)=1
),
moda_saida_geral AS (
  SELECT id_usuario, dia_mes, COUNT(*) AS qtd
  FROM saidas_todas GROUP BY id_usuario, dia_mes
  QUALIFY ROW_NUMBER() OVER(PARTITION BY id_usuario ORDER BY qtd DESC, dia_mes ASC)=1
),
dia_vencimento_cliente AS (
  SELECT c.id_usuario,
    COALESCE(mv.dia_mes, ms.dia_mes, 1) AS dia_vencimento
  FROM clientes c
  LEFT JOIN moda_vencimento mv USING(id_usuario)
  LEFT JOIN moda_saida_geral ms USING(id_usuario)
),
-- Ciclos: usamos as datas reais de pagamento de fatura como proxy do
-- vencimento de cada ciclo (simplificação; ver docs/regras-negocio.md).
-- ciclo 3 = mais recente (ciclo corrente, ainda "não pago" na simulação),
-- ciclo 1 = o mais antigo dos três últimos.
pagamentos_recentes AS (
  SELECT id_usuario, data_pagto,
    ROW_NUMBER() OVER(PARTITION BY id_usuario ORDER BY data_pagto DESC) AS rn_desc
  FROM faturas
),
ciclos_pivot AS (
  SELECT id_usuario,
    MAX(IF(rn_desc=3, data_pagto, NULL)) AS venc_1,
    MAX(IF(rn_desc=2, data_pagto, NULL)) AS venc_2,
    MAX(IF(rn_desc=1, data_pagto, NULL)) AS venc_3
  FROM pagamentos_recentes
  WHERE rn_desc <= 3
  GROUP BY id_usuario
),
ciclos_definicao AS (
  SELECT id_usuario, 1 AS ciclo, j.inicio_janela AS data_inicio, venc_1 AS data_fim
  FROM ciclos_pivot CROSS JOIN janela j
  WHERE venc_1 IS NOT NULL
  UNION ALL
  SELECT id_usuario, 2, COALESCE(venc_1, j.inicio_janela), venc_2
  FROM ciclos_pivot CROSS JOIN janela j
  WHERE venc_2 IS NOT NULL
  UNION ALL
  SELECT id_usuario, 3, COALESCE(venc_2, venc_1, j.inicio_janela), venc_3
  FROM ciclos_pivot CROSS JOIN janela j
  WHERE venc_3 IS NOT NULL
),
-- entradas/saídas de cada ciclo completo (do início ao vencimento), excluindo
-- pagamento de fatura e compras no cartão (ambos são saída de outro fluxo).
fluxos_ciclo AS (
  SELECT cd.id_usuario, cd.ciclo, cd.data_fim,
    SUM(CASE WHEN UPPER(t.tipo)='E' THEN t.vlr ELSE 0 END) AS entradas,
    SUM(CASE WHEN UPPER(t.tipo)='S'
      AND IFNULL(t.nom_cate_micro, '') != 'Pagamento de fatura'
      AND NOT (LOWER(COALESCE(t.descr,'')) LIKE '%cart credito%' AND LOWER(COALESCE(t.descr,'')) NOT LIKE '%pag%fat%')
      THEN t.vlr ELSE 0 END) AS saidas_nao_cartao
  FROM ciclos_definicao cd
  LEFT JOIN `batalha-time-05-xew3.hackathon_dados.cash90_hackathon` t
    ON t.id_usuario = cd.id_usuario
    AND DATE(t.anomesdia) > cd.data_inicio AND DATE(t.anomesdia) <= cd.data_fim
  GROUP BY cd.id_usuario, cd.ciclo, cd.data_fim
),
-- R19: para o ciclo corrente (3), os últimos 5 dias antes do vencimento
-- (após `data_simulada`) são substituídos pela média dos mesmos 5 dias finais
-- dos dois ciclos anteriores, em vez do fluxo real (que ainda não é
-- "conhecido" na data simulada).
cauda_historica AS (
  SELECT cd.id_usuario,
    SUM(CASE WHEN UPPER(t.tipo)='E' THEN t.vlr ELSE 0 END) AS entradas_cauda,
    SUM(CASE WHEN UPPER(t.tipo)='S'
      AND IFNULL(t.nom_cate_micro, '') != 'Pagamento de fatura'
      AND NOT (LOWER(COALESCE(t.descr,'')) LIKE '%cart credito%' AND LOWER(COALESCE(t.descr,'')) NOT LIKE '%pag%fat%')
      THEN t.vlr ELSE 0 END) AS saidas_cauda,
    COUNT(DISTINCT cd.ciclo) AS ciclos_com_cauda
  FROM ciclos_definicao cd
  LEFT JOIN `batalha-time-05-xew3.hackathon_dados.cash90_hackathon` t
    ON t.id_usuario = cd.id_usuario
    AND DATE(t.anomesdia) > DATE_SUB(cd.data_fim, INTERVAL 5 DAY)
    AND DATE(t.anomesdia) <= cd.data_fim
  WHERE cd.ciclo IN (1,2)
  GROUP BY cd.id_usuario
),
ciclo_atual_parcial AS (
  SELECT cd.id_usuario,
    DATE_SUB(cd.data_fim, INTERVAL 5 DAY) AS data_simulada,
    SUM(CASE WHEN UPPER(t.tipo)='E' THEN t.vlr ELSE 0 END) AS entradas_parcial,
    SUM(CASE WHEN UPPER(t.tipo)='S'
      AND IFNULL(t.nom_cate_micro, '') != 'Pagamento de fatura'
      AND NOT (LOWER(COALESCE(t.descr,'')) LIKE '%cart credito%' AND LOWER(COALESCE(t.descr,'')) NOT LIKE '%pag%fat%')
      THEN t.vlr ELSE 0 END) AS saidas_parcial
  FROM ciclos_definicao cd
  LEFT JOIN `batalha-time-05-xew3.hackathon_dados.cash90_hackathon` t
    ON t.id_usuario = cd.id_usuario
    AND DATE(t.anomesdia) > cd.data_inicio
    AND DATE(t.anomesdia) <= DATE_SUB(cd.data_fim, INTERVAL 5 DAY)
  WHERE cd.ciclo = 3
  GROUP BY cd.id_usuario, cd.data_fim
),
saldo_por_ciclo AS (
  SELECT fc.id_usuario, fc.ciclo, fc.data_fim,
    CASE WHEN fc.ciclo = 3 THEN
      (COALESCE(cap.entradas_parcial,0) + COALESCE(SAFE_DIVIDE(ch.entradas_cauda, ch.ciclos_com_cauda),0))
      - (COALESCE(cap.saidas_parcial,0) + COALESCE(SAFE_DIVIDE(ch.saidas_cauda, ch.ciclos_com_cauda),0))
    ELSE fc.entradas - fc.saidas_nao_cartao
    END AS saldo_previsto_vencimento
  FROM fluxos_ciclo fc
  LEFT JOIN ciclo_atual_parcial cap USING(id_usuario)
  LEFT JOIN cauda_historica ch USING(id_usuario)
),
-- fatura estimada de cada ciclo = 1.33x as compras de cartão do mês anterior
-- ao mês do vencimento daquele ciclo (mesma regra de `silver_cartao`).
fatura_por_ciclo AS (
  SELECT sp.id_usuario, sp.ciclo, sp.saldo_previsto_vencimento,
    sc.fatura_estimada,
    GREATEST(COALESCE(sc.fatura_estimada,0) - sp.saldo_previsto_vencimento, 0) AS falta
  FROM saldo_por_ciclo sp
  LEFT JOIN `batalha-time-05-xew3.hackathon_dados.silver_cartao` sc
    ON sc.id_usuario = sp.id_usuario
    AND sc.anomes = CAST(FORMAT_DATE('%Y%m', sp.data_fim) AS INT64)
),
resumo_ciclos AS (
  SELECT id_usuario,
    COUNTIF(falta > 0) AS ciclos_com_falta,
    COUNT(*) AS ciclos_disponiveis,
    MAX(CASE WHEN ciclo=3 THEN saldo_previsto_vencimento END) AS saldo_atual,
    MAX(CASE WHEN ciclo=3 THEN falta END) AS falta_atual,
    MAX(CASE WHEN ciclo=3 THEN fatura_estimada END) AS fatura_atual
  FROM fatura_por_ciclo
  GROUP BY id_usuario
)
SELECT
  c.id_usuario,
  dv.dia_vencimento,
  -- sem pagamento de fatura na janela: usa fim da janela - 5 dias.
  COALESCE(cap.data_simulada, DATE_SUB(j.fim_janela, INTERVAL 5 DAY)) AS data_simulada,
  COALESCE(rc.saldo_atual, 0) AS saldo_previsto_vencimento,
  COALESCE(rc.fatura_atual, 0) AS fatura_estimada,
  COALESCE(rc.falta_atual, rc.fatura_atual, 0) AS valor_faltante,
  CASE
    WHEN COALESCE(rc.falta_atual, 0) = 0 THEN 'SEM_FALTA'
    WHEN COALESCE(rc.ciclos_com_falta, 0) >= 2 THEN 'RECORRENTE'
    ELSE 'PONTUAL'
  END AS tipo_falta
FROM clientes c
CROSS JOIN janela j
LEFT JOIN dia_vencimento_cliente dv USING(id_usuario)
LEFT JOIN resumo_ciclos rc USING(id_usuario)
LEFT JOIN ciclo_atual_parcial cap USING(id_usuario);
