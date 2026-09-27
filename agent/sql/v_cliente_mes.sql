-- Nome equivalente no contrato de docs/10-contrato-dados-gold.md: `gold_cliente_mes` (mesmas definições; escolher um nome com o Lucas).
-- v_cliente_mes: agregados por cliente e mês usados pelo motor de capacidade (cabe_core.capacidade.motor).
-- Valores em centavos (INT64). Mesmas definições do CSV:
--   renda_total        = todas as entradas (tipo E), inclusive PIX (definição de analise/persona_b.py)
--   renda_recorrente   = 'Salario CLT' + 'Beneficio INSS' (o que o motor usa como renda; PIX/13º/PLR viram flag)
--   fixos              = subcategorias fixas pagas pela conta (fora do cartão); fixos_total inclui as no cartão (persona_b)
--   essenciais         = macros essenciais, sem as micros já contadas em fixos e sem compras no cartão (já estão na fatura)
--   cartao             = compras no cartão (descr começa com 'cart credito'); entram na fatura do mês seguinte
-- Uma leitura por cliente por sessão (WHERE id_usuario = @cliente_id na consulta que usa a view); nunca em loop.
CREATE OR REPLACE VIEW `batalha-time-05-xew3.hackathon_dados.v_cliente_mes` AS
WITH base AS (
  SELECT
    id_usuario,
    anomes,
    EXTRACT(DAY FROM anomesdia) AS dia,
    tipo,
    IFNULL(descr, '') AS descr,
    CAST(ROUND(vlr * 100) AS INT64) AS c,
    IFNULL(nom_cate_macro, '') AS macro,
    IFNULL(nom_cate_micro, '') AS micro,
    STARTS_WITH(IFNULL(descr, ''), 'cart credito') AS cartao,
    IFNULL(nom_cate_micro, '') IN (
      'Mensalidade escolar', 'Condominio', 'Seguro de automovel', 'Energia eletrica', 'Agua e esgoto', 'Gas',
      'TV Internet celular e telefone', 'Celular', 'Emprestimos', 'Outros emprestimos', 'Consorcio',
      'Pagamento de aluguel', 'Financiamento de imovel'
    ) AS eh_fixo,
    IFNULL(nom_cate_macro, '') IN (
      'Mercado', 'Posto de combustivel', 'Transporte publico', 'Cuidados pessoais', 'Educacao', 'Pets', 'Casa'
    ) AS eh_essencial,
    IFNULL(nom_cate_micro, '') IN ('Salario CLT', 'Beneficio INSS') AS eh_recorrente
  FROM `batalha-time-05-xew3.hackathon_dados.extrato_sintetico`
)
SELECT
  id_usuario,
  anomes,
  COUNT(*) AS n_lancamentos,
  SUM(IF(tipo = 'E', c, 0)) AS renda_total,
  SUM(IF(tipo = 'E' AND eh_recorrente, c, 0)) AS renda_recorrente,
  MIN(IF(tipo = 'E' AND eh_recorrente, dia, NULL)) AS dia_recebimento,
  SUM(IF(tipo = 'E' AND micro = 'Salario CLT', c, 0)) AS salario,
  SUM(IF(tipo = 'E' AND micro = 'Beneficio INSS', c, 0)) AS beneficio_inss,
  SUM(IF(tipo = 'E' AND macro = 'Recebimentos diversos', c, 0)) AS pix_recebido,
  SUM(IF(tipo = 'E' AND micro IN ('13o salario', 'Bonus PLR'), c, 0)) AS decimo_terceiro_plr,
  SUM(IF(tipo = 'E' AND micro = 'Recebimento Aluguel', c, 0)) AS aluguel_recebido,
  SUM(IF(tipo = 'S' AND eh_fixo AND NOT cartao, c, 0)) AS fixos,
  SUM(IF(eh_fixo, c, 0)) AS fixos_total,
  SUM(IF(tipo = 'S' AND eh_essencial AND NOT eh_fixo AND NOT cartao, c, 0)) AS essenciais,
  SUM(IF(cartao, c, 0)) AS cartao,
  SUM(IF(macro IN ('Delivery', 'Transporte por app'), c, 0)) AS delivery_app,
  SUM(IF(micro = 'Juros pagos', c, 0)) AS juros_pagos,
  SUM(IF(micro = 'Pagamento de fatura', c, 0)) AS pagamento_fatura,
  SUM(IF(cartao AND IFNULL(parcela_total, 0) > 1, c, 0)) AS cartao_parcelas_em_curso
FROM base
GROUP BY id_usuario, anomes;
