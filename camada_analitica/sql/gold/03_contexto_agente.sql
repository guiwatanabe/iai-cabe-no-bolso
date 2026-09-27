CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.gold_contexto_agente` AS
SELECT id_usuario, grupo_cliente, fatura_estimada, saldo_atual, fatura_cabe,
  valor_faltante, tipo_falta, renda_mensal_estimada, folga_mensal_estimada,
  qtd_pedaladas_12m, publico_mvp, elegivel_cobertura_curta,
  elegivel_parcelamento, acao_motor,
  CASE WHEN fatura_cabe=TRUE THEN 'SEM_INTERVENCAO'
       WHEN elegivel_cobertura_curta=TRUE THEN 'COBERTURA_CURTA'
       WHEN elegivel_parcelamento=TRUE THEN 'CONSULTAR_CREDITO'
       ELSE 'SEM_OFERTA_AUTOMATICA' END AS recomendacao_motor
FROM `batalha-time-05-xew3.hackathon_dados.gold_elegibilidade`;
