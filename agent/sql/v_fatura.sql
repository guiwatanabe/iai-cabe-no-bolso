-- Nome equivalente no contrato de docs/10-contrato-dados-gold.md: `gold_fatura_mes` (mesmas definições; escolher um nome com o Lucas).
-- v_fatura: uma linha por cliente e mês com o pagamento da fatura, o modo, os juros do mês e a fatura reconstruída por modo.
-- Equivale a cabe_core.dados._faturas_de + cabe_core.fatura.reconstruir (docs/01 §3). Não executar em loop: uma consulta por cliente.
-- Observação: ROUND do BigQuery arredonda 0,5 para longe do zero; o Python usa round half-even. Diferença possível de 1 centavo
-- em casos exatos de meio centavo (raríssimos na base).
CREATE OR REPLACE VIEW `batalha-time-05-xew3.hackathon_dados.v_fatura` AS
WITH pagamento AS (
  SELECT
    id_usuario,
    anomes,
    EXTRACT(DAY FROM anomesdia) AS dia_vencimento,
    CAST(ROUND(vlr * 100) AS INT64) AS pago,
    CASE
      WHEN LOWER(IFNULL(descr, '')) LIKE '%minimo%'  THEN 'minimo'
      WHEN LOWER(IFNULL(descr, '')) LIKE '%parcial%' THEN 'parcial'
      ELSE 'integral'
    END AS modo,
    ROW_NUMBER() OVER (PARTITION BY id_usuario, anomes ORDER BY anomesdia) AS rn
  FROM `batalha-time-05-xew3.hackathon_dados.extrato_sintetico`
  WHERE nom_cate_micro = 'Pagamento de fatura'
),
juros AS (
  SELECT id_usuario, anomes, SUM(CAST(ROUND(vlr * 100) AS INT64)) AS juros
  FROM `batalha-time-05-xew3.hackathon_dados.extrato_sintetico`
  WHERE nom_cate_micro = 'Juros pagos'
  GROUP BY id_usuario, anomes
)
SELECT
  p.id_usuario,
  p.anomes,
  p.dia_vencimento,
  p.modo,
  p.pago,
  IFNULL(j.juros, 0) AS juros,
  CASE p.modo
    WHEN 'integral' THEN p.pago
    WHEN 'minimo'   THEN CAST(ROUND(p.pago / 0.15) AS INT64)
    ELSE CAST(ROUND(p.pago + IFNULL(j.juros, 0) / 0.14) AS INT64)
  END AS fatura,
  CASE p.modo
    WHEN 'integral' THEN 0
    WHEN 'minimo'   THEN CAST(ROUND(p.pago / 0.15) AS INT64) - p.pago
    ELSE CAST(ROUND(p.pago + IFNULL(j.juros, 0) / 0.14) AS INT64) - p.pago
  END AS nao_pago,
  p.modo IN ('minimo', 'parcial') AS rolada,
  -- nos meses integrais os "Juros pagos" são de cheque especial, não da fatura
  CASE WHEN p.modo = 'integral' AND IFNULL(j.juros, 0) > 0 THEN IFNULL(j.juros, 0) ELSE 0 END AS juros_cheque_especial
FROM pagamento p
LEFT JOIN juros j USING (id_usuario, anomes)
WHERE p.rn = 1;
