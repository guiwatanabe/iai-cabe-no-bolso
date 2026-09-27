-- Checa a distribuição de `tipo_falta` por `grupo_cliente` (segmentação de 12
-- meses). Esperado, de acordo com o plano (seção 5): monotônico —
-- SEMPRE_QUITA majoritariamente SEM_FALTA, ESCORREGAO majoritariamente
-- PONTUAL, ROLANDO_FATURA majoritariamente RECORRENTE. Se a distribuição não
-- for monotônica, reportar em vez de ajustar a fórmula silenciosamente.
SELECT grupo_cliente, tipo_falta, COUNT(*) AS clientes,
  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY grupo_cliente), 1) AS pct_do_grupo
FROM `batalha-time-05-xew3.hackathon_dados.gold_contexto_agente`
GROUP BY grupo_cliente, tipo_falta
ORDER BY grupo_cliente, tipo_falta;
