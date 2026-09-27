-- Nome equivalente no contrato de docs/10-contrato-dados-gold.md: `gold_perfil` (mesmas definições; escolher um nome com o Lucas).
-- v_perfil: uma linha por cliente com flags de perfil, faturas roladas no ano, maior sequência e grupo.
-- Equivale a cabe_core.dados._perfil_de + cabe_core.grupo.classificar sobre o ano inteiro (visão exploratória;
-- o motor calcula o grupo pelos 12 meses ANTES do mês da fatura, sem olhar o futuro).
CREATE OR REPLACE VIEW `batalha-time-05-xew3.hackathon_dados.v_perfil` AS
WITH entradas AS (
  SELECT
    id_usuario,
    LOGICAL_OR(nom_cate_micro = 'Salario CLT')          AS tem_salario_clt,
    LOGICAL_OR(nom_cate_micro = 'Beneficio INSS')       AS tem_inss,
    LOGICAL_OR(nom_cate_micro = 'Recebimento Aluguel')  AS recebe_aluguel,
    LOGICAL_OR(nom_cate_micro = '13o salario')          AS tem_13o,
    LOGICAL_OR(nom_cate_micro = 'Bonus PLR')            AS tem_plr
  FROM `batalha-time-05-xew3.hackathon_dados.extrato_sintetico`
  WHERE tipo = 'E'
  GROUP BY id_usuario
),
saidas AS (
  SELECT
    id_usuario,
    LOGICAL_OR(nom_cate_micro = 'Financiamento de imovel') AS tem_financiamento_imovel,
    LOGICAL_OR(nom_cate_micro = 'Pagamento de aluguel')    AS paga_aluguel
  FROM `batalha-time-05-xew3.hackathon_dados.extrato_sintetico`
  GROUP BY id_usuario
),
salario AS (
  SELECT
    id_usuario,
    APPROX_TOP_COUNT(EXTRACT(DAY FROM anomesdia), 1)[OFFSET(0)].value AS dia_recebimento,
    APPROX_QUANTILES(CAST(ROUND(vlr * 100) AS INT64), 2)[OFFSET(1)] AS recebimento_mediano
  FROM `batalha-time-05-xew3.hackathon_dados.extrato_sintetico`
  WHERE tipo = 'E' AND nom_cate_micro IN ('Salario CLT', 'Beneficio INSS')
  GROUP BY id_usuario
),
-- ilhas de meses rolados seguidos (gaps-and-islands) para a maior sequência
fat AS (
  SELECT id_usuario, anomes, rolada,
         ROW_NUMBER() OVER (PARTITION BY id_usuario ORDER BY anomes)
         - ROW_NUMBER() OVER (PARTITION BY id_usuario, rolada ORDER BY anomes) AS ilha
  FROM `batalha-time-05-xew3.hackathon_dados.v_fatura`
),
sequencias AS (
  SELECT id_usuario, MAX(tamanho) AS maior_sequencia
  FROM (SELECT id_usuario, ilha, COUNT(*) AS tamanho FROM fat WHERE rolada GROUP BY id_usuario, ilha)
  GROUP BY id_usuario
),
faturas AS (
  SELECT
    id_usuario,
    COUNT(*) AS meses_com_fatura,
    COUNTIF(rolada) AS meses_rolados,
    SUM(juros) AS juros_ano,
    SUM(IF(rolada, juros, 0)) AS juros_rotativo_ano,
    SUM(juros_cheque_especial) AS juros_cheque_especial_ano,
    SUM(nao_pago) AS nao_pago_ano,
    APPROX_QUANTILES(fatura, 2)[OFFSET(1)] AS fatura_mediana,
    APPROX_TOP_COUNT(dia_vencimento, 1)[OFFSET(0)].value AS dia_vencimento
  FROM `batalha-time-05-xew3.hackathon_dados.v_fatura`
  GROUP BY id_usuario
)
SELECT
  f.id_usuario,
  CASE
    WHEN e.tem_salario_clt THEN 'clt'
    WHEN e.tem_inss THEN 'inss'
    WHEN NOT IFNULL(e.tem_salario_clt, FALSE) AND NOT IFNULL(e.tem_inss, FALSE) THEN 'pix'
    ELSE 'outro'
  END AS tipo_renda,
  IFNULL(s.tem_financiamento_imovel, FALSE) AS tem_financiamento_imovel,
  IFNULL(s.paga_aluguel, FALSE) AS paga_aluguel,
  IFNULL(e.recebe_aluguel, FALSE) AS recebe_aluguel,
  IFNULL(e.tem_13o, FALSE) AS tem_13o,
  IFNULL(e.tem_plr, FALSE) AS tem_plr,
  sal.dia_recebimento,
  sal.recebimento_mediano,
  f.dia_vencimento,
  f.meses_com_fatura,
  f.meses_rolados,
  IFNULL(q.maior_sequencia, 0) AS maior_sequencia,
  f.juros_ano,
  f.juros_rotativo_ano,
  f.juros_cheque_especial_ano,
  f.nao_pago_ano,
  f.fatura_mediana,
  CASE
    WHEN f.meses_rolados = 0 THEN 'em_dia'
    WHEN f.meses_rolados <= 2 THEN 'escorregao'
    WHEN f.meses_rolados <= 5 THEN 'rolando'
    ELSE 'no_limite'
  END AS grupo,
  CASE
    WHEN f.meses_rolados = 0 THEN 'nunca'
    WHEN f.meses_rolados <= 2 THEN 'A'
    WHEN f.meses_rolados <= 5 THEN 'B'
    ELSE 'C'
  END AS grupo_letra
FROM faturas f
LEFT JOIN entradas e USING (id_usuario)
LEFT JOIN saidas s USING (id_usuario)
LEFT JOIN salario sal USING (id_usuario)
LEFT JOIN sequencias q USING (id_usuario);
