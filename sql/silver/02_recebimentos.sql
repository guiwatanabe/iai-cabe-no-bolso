-- apliquei as correções mantendo a estrutura original, mas corrigi os dois pontos que identificamos (renda recorrente sem 13º e valor típico baseado prioritariamente em folha/INSS).
CREATE OR REPLACE TABLE
  `batalha-time-05-xew3.hackathon_dados.silver_recebimentos`
AS

WITH entradas AS (

  SELECT
    id_usuario,
    DATE(anomesdia) AS data,
    vlr,
    EXTRACT(DAY FROM DATE(anomesdia)) AS dia_mes,
    COALESCE(nom_cate_micro, '') AS nom_cate_micro,
    LOWER(COALESCE(descr, '')) AS descricao

  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`

  WHERE UPPER(tipo) = 'E'
),

/* ============================================================
   1. Identificação das entradas recorrentes de folha
   ============================================================ */

entradas_classificadas AS (

  SELECT
    *,

    CASE
      WHEN nom_cate_micro IN ('Salario CLT', 'Beneficio INSS')
      THEN TRUE
      ELSE FALSE
    END AS flag_folha,

    /*
      13º não deve ser interpretado como renda mensal recorrente.

      Mantemos a transação na base, mas retiramos da estimativa
      de renda e do recebimento típico.
    */
    CASE
      WHEN
        REGEXP_CONTAINS(
          descricao,
          r'13[oº]?|13 salario|13o salario|decimo terceiro|décimo terceiro'
        )
      THEN TRUE
      ELSE FALSE
    END AS flag_13_salario

  FROM entradas
),

/* ============================================================
   2. Folha recorrente válida
   ============================================================ */

folha_recorrente AS (

  SELECT *

  FROM entradas_classificadas

  WHERE flag_folha = TRUE
    AND flag_13_salario = FALSE
),

/* ============================================================
   3. Dia típico de recebimento
      Prioridade: Salário CLT / INSS
   ============================================================ */

moda_folha AS (

  SELECT
    id_usuario,
    dia_mes,
    COUNT(*) AS qtd

  FROM folha_recorrente

  GROUP BY
    id_usuario,
    dia_mes

  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY id_usuario
    ORDER BY qtd DESC, dia_mes ASC
  ) = 1
),

/* ============================================================
   4. Fallback para clientes sem folha identificada
   ============================================================ */

moda_geral AS (

  SELECT
    id_usuario,
    dia_mes,
    COUNT(*) AS qtd

  FROM entradas_classificadas

  WHERE flag_13_salario = FALSE

  GROUP BY
    id_usuario,
    dia_mes

  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY id_usuario
    ORDER BY qtd DESC, dia_mes ASC
  ) = 1
),

/* ============================================================
   5. Estatísticas da folha
   ============================================================ */

estatisticas_folha AS (

  SELECT
    id_usuario,

    APPROX_QUANTILES(vlr, 100)[OFFSET(50)]
      AS valor_recebimento_tipico_folha,

    SUM(vlr)
      AS recebimentos_folha_90d,

    COUNT(DISTINCT FORMAT_DATE('%Y-%m', data))
      AS meses_com_folha

  FROM folha_recorrente

  GROUP BY id_usuario
),

/* ============================================================
   6. Estatísticas gerais
      Fallback para clientes sem folha/INSS
   ============================================================ */

estatisticas_gerais AS (

  SELECT
    id_usuario,

    SUM(vlr) AS entradas_90d,

    APPROX_QUANTILES(
      IF(flag_13_salario = FALSE, vlr, NULL),
      100
    )[OFFSET(50)] AS valor_recebimento_tipico_geral,

    SUM(
      CASE
        WHEN flag_13_salario = FALSE
        THEN vlr
        ELSE 0
      END
    ) AS entradas_recorrentes_90d,

    COUNT(*) AS qtd_entradas_90d,

    COUNT(
      DISTINCT CASE
        WHEN flag_13_salario = FALSE
        THEN FORMAT_DATE('%Y-%m', data)
      END
    ) AS meses_com_entrada,

    MAX(data) AS ultimo_recebimento

  FROM entradas_classificadas

  GROUP BY id_usuario
),

/* ============================================================
   7. Público vulnerável
   ============================================================ */

vulneravel AS (

  SELECT
    id_usuario,

    LOGICAL_OR(
      nom_cate_micro = 'Beneficio INSS'
    ) AS publico_vulneravel

  FROM entradas_classificadas

  GROUP BY id_usuario
)

/* ============================================================
   OUTPUT
   ============================================================ */

SELECT
  e.id_usuario,

  /* Total observado continua disponível para análise */
  e.entradas_90d,

  /*
    Renda mensal:
    - se existe folha → utiliza folha recorrente;
    - caso contrário → utiliza entradas sem 13º.
  */
  CASE
    WHEN COALESCE(f.meses_com_folha, 0) > 0
    THEN SAFE_DIVIDE(
      f.recebimentos_folha_90d,
      f.meses_com_folha
    )

    ELSE SAFE_DIVIDE(
      e.entradas_recorrentes_90d,
      NULLIF(e.meses_com_entrada, 0)
    )
  END AS renda_mensal_estimada,

  /*
    Valor típico:
    prioriza folha/INSS.
  */
  COALESCE(
    f.valor_recebimento_tipico_folha,
    e.valor_recebimento_tipico_geral
  ) AS valor_recebimento_tipico,

  /*
    Dia esperado:
    prioriza folha/INSS.
  */
  COALESCE(
    mf.dia_mes,
    mg.dia_mes
  ) AS dia_recebimento_estimado,

  e.qtd_entradas_90d,
  e.meses_com_entrada,
  e.ultimo_recebimento,

  CASE
    WHEN COALESCE(f.meses_com_folha, e.meses_com_entrada) >= 3
      THEN 'ALTA'

    WHEN COALESCE(f.meses_com_folha, e.meses_com_entrada) = 2
      THEN 'MEDIA'

    ELSE 'BAIXA'
  END AS confianca_recebimento,

  COALESCE(
    v.publico_vulneravel,
    FALSE
  ) AS publico_vulneravel

FROM estatisticas_gerais e

LEFT JOIN estatisticas_folha f
  USING (id_usuario)

LEFT JOIN moda_folha mf
  USING (id_usuario)

LEFT JOIN moda_geral mg
  USING (id_usuario)

LEFT JOIN vulneravel v
  USING (id_usuario);
