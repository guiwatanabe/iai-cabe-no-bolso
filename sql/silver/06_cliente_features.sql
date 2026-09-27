CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.silver_cliente_features` AS
WITH cash AS (
  SELECT id_usuario,
    SUM(entrada_dia) AS entradas_90d, SUM(saida_dia) AS saidas_90d,
    SUM(fluxo_liquido_dia) AS fluxo_90d, AVG(saida_dia) AS media_saida_dia,
    MAX(data) AS data_referencia
  FROM `batalha-time-05-xew3.hackathon_dados.silver_cliente_dia`
  GROUP BY id_usuario
),
cartao_atual AS (
  SELECT * FROM `batalha-time-05-xew3.hackathon_dados.silver_cartao`
  QUALIFY ROW_NUMBER() OVER(PARTITION BY id_usuario ORDER BY anomes DESC)=1
)
SELECT c.id_usuario,
  h.grupo_cliente, h.qtd_pedaladas_12m, h.qtd_minimos_12m, h.ultima_pedalada,
  c.data_referencia, c.entradas_90d, c.saidas_90d, c.fluxo_90d,
  r.renda_mensal_estimada, r.dia_recebimento_estimado,
  r.valor_recebimento_tipico, r.confianca_recebimento,
  cp.gasto_recorrente_mensal_estimado, cp.parcelas_futuras_estimadas,
  ca.compras_cartao_mes, ca.fatura_estimada,
  r.renda_mensal_estimada-cp.gasto_recorrente_mensal_estimado AS folga_mensal_estimada
FROM cash c
LEFT JOIN `batalha-time-05-xew3.hackathon_dados.silver_recebimentos` r USING(id_usuario)
LEFT JOIN `batalha-time-05-xew3.hackathon_dados.silver_historico_fatura_12m` h USING(id_usuario)
LEFT JOIN `batalha-time-05-xew3.hackathon_dados.silver_compromissos` cp USING(id_usuario)
LEFT JOIN cartao_atual ca USING(id_usuario);
