CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.gold_capacidade_pagamento` AS
SELECT f.*,
  cf.dia_vencimento, cf.data_simulada, cf.fatura_estimada,
  cf.saldo_previsto_vencimento, cf.valor_faltante, cf.tipo_falta,
  -- R16: dias entre o vencimento e o próximo recebimento esperado (ambos
  -- expressos como dia do mês; ciclo de ~30 dias).
  MOD(f.dia_recebimento_estimado - cf.dia_vencimento + 30, 30) AS dias_ate_recebimento,
  CASE WHEN cf.valor_faltante<=0 THEN TRUE ELSE FALSE END AS fatura_cabe
FROM `batalha-time-05-xew3.hackathon_dados.silver_cliente_features` f
JOIN `batalha-time-05-xew3.hackathon_dados.silver_ciclo_fatura` cf USING(id_usuario);
