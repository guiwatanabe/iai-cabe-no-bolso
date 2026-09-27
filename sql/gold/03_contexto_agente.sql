-- Contrato: colunas exatas de `mcp_server/core/tipos.py::Contexto`.
-- Dinheiro em centavos INT64; uma linha por cliente.
CREATE OR REPLACE TABLE `batalha-time-05-xew3.hackathon_dados.gold_contexto_agente` AS
SELECT
  id_usuario,
  data_simulada,
  grupo_cliente,
  qtd_pedaladas_12m,
  dia_vencimento,
  CAST(ROUND(fatura_estimada*100) AS INT64) AS fatura_estimada_c,
  CAST(ROUND(saldo_previsto_vencimento*100) AS INT64) AS saldo_previsto_vencimento_c,
  CAST(ROUND(renda_mensal_estimada*100) AS INT64) AS renda_mensal_estimada_c,
  CAST(ROUND(valor_recebimento_tipico*100) AS INT64) AS valor_recebimento_tipico_c,
  dia_recebimento_estimado,
  dias_ate_recebimento,
  confianca_recebimento,
  CAST(ROUND(folga_mensal_estimada*100) AS INT64) AS folga_mensal_c,
  CAST(ROUND(valor_faltante*100) AS INT64) AS valor_faltante_c,
  tipo_falta,
  CAST(ROUND(parcelas_em_curso*100) AS INT64) AS parcelas_em_curso_c,
  publico_vulneravel,
  elegivel_cobertura_curta,
  elegivel_parcelamento
FROM `batalha-time-05-xew3.hackathon_dados.gold_elegibilidade`;
