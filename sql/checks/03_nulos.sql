-- Checa ausência de NULL em todas as colunas obrigatórias de
-- `mcp_server/core/tipos.py::Contexto` (nenhuma delas é opcional no schema).
SELECT COUNT(*) AS linhas_com_nulo
FROM `batalha-time-05-xew3.hackathon_dados.gold_contexto_agente`
WHERE id_usuario IS NULL
   OR data_simulada IS NULL
   OR grupo_cliente IS NULL
   OR qtd_pedaladas_12m IS NULL
   OR dia_vencimento IS NULL
   OR fatura_estimada_c IS NULL
   OR saldo_previsto_vencimento_c IS NULL
   OR renda_mensal_estimada_c IS NULL
   OR valor_recebimento_tipico_c IS NULL
   OR dia_recebimento_estimado IS NULL
   OR dias_ate_recebimento IS NULL
   OR confianca_recebimento IS NULL
   OR folga_mensal_c IS NULL
   OR valor_faltante_c IS NULL
   OR tipo_falta IS NULL
   OR parcelas_em_curso_c IS NULL
   OR publico_vulneravel IS NULL
   OR elegivel_cobertura_curta IS NULL
   OR elegivel_parcelamento IS NULL;
-- esperado: 0
