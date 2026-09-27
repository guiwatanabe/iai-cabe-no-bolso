-- Checa que `gold_contexto_agente` tem exatamente uma linha por cliente e
-- que a base bate com o universo de clientes da janela D-90.
SELECT
  (SELECT COUNT(*) FROM `batalha-time-05-xew3.hackathon_dados.gold_contexto_agente`) AS linhas_gold,
  (SELECT COUNT(DISTINCT id_usuario) FROM `batalha-time-05-xew3.hackathon_dados.gold_contexto_agente`) AS clientes_distintos_gold,
  (SELECT COUNT(DISTINCT id_usuario) FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`) AS clientes_janela_d90;
-- esperado: linhas_gold = clientes_distintos_gold = clientes_janela_d90
