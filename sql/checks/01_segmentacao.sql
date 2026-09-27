-- Checa se a segmentação de 12 meses continua 311/101/384/204 (SEMPRE_QUITA/
-- ESCORREGAO/ROLANDO_FATURA/NO_LIMITE) após as mudanças de R3/R5/R11/R15-19,
-- que não tocam a regra de segmentação (`silver_historico_fatura_12m`).
SELECT grupo_cliente, COUNT(*) AS clientes
FROM `batalha-time-05-xew3.hackathon_dados.gold_contexto_agente`
GROUP BY grupo_cliente
ORDER BY grupo_cliente;
-- esperado: ESCORREGAO=101, NO_LIMITE=204, ROLANDO_FATURA=384, SEMPRE_QUITA=311
