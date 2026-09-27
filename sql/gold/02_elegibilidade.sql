CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.gold_elegibilidade` AS
SELECT *,
  CASE WHEN grupo_cliente IN ('ESCORREGAO','ROLANDO_FATURA') THEN TRUE ELSE FALSE END AS publico_mvp,
  CASE WHEN grupo_cliente='ESCORREGAO' AND tipo_falta='PONTUAL'
    AND valor_faltante>0 AND valor_faltante<=folga_mensal_estimada
    THEN TRUE ELSE FALSE END AS elegivel_cobertura_curta,
  CASE WHEN grupo_cliente='ROLANDO_FATURA' AND tipo_falta='RECORRENTE'
    AND folga_mensal_estimada>0 THEN TRUE ELSE FALSE END AS elegivel_parcelamento,
  CASE WHEN grupo_cliente='NO_LIMITE' THEN 'FORA_MVP'
       WHEN fatura_cabe=TRUE THEN 'FATURA_CABE'
       WHEN grupo_cliente='ESCORREGAO' THEN 'AVALIAR_COBERTURA_CURTA'
       WHEN grupo_cliente='ROLANDO_FATURA' THEN 'AVALIAR_PARCELAMENTO'
       ELSE 'SEM_ACAO' END AS acao_motor
FROM `batalha-time-05-xew3.hackathon_dados.gold_capacidade_pagamento`;
