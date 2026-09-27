/*
ALTERAÇÕES V3:
- 13º excluído das entradas recorrentes.
- Ciclo 1 parcial não entra na recorrência.
- Removido carry-over entre ciclos.
- Saldo substituído por caixa disponível estimado.
- Separados déficit pré-fatura, falta da fatura e déficit total.
- PONTUAL/RECORRENTE definidos pelos ciclos completos recentes.
*/
CREATE OR REPLACE TABLE
  `batalha-time-05-xew3.hackathon_dados.silver_ciclo_fatura`
AS

WITH janela AS (

  SELECT
    MIN(DATE(anomesdia)) AS inicio_janela,
    MAX(DATE(anomesdia)) AS fim_janela

  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
),

clientes AS (

  SELECT DISTINCT id_usuario

  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon`
),

/* ============================================================
   1. TRANSAÇÕES

   - data_movimento evita ambiguidade com outras colunas "data"
   - 13º é identificado para não entrar como renda recorrente
   ============================================================ */

transacoes AS (

  SELECT
    t.*,

    DATE(t.anomesdia) AS data_movimento,

    REGEXP_CONTAINS(
      LOWER(COALESCE(t.descr, '')),
      r'13[oº]?|13 salario|13o salario|decimo terceiro|décimo terceiro'
    ) AS flag_13_salario

  FROM `batalha-time-05-xew3.hackathon_dados.cash90_hackathon` t
),

/* ============================================================
   2. PAGAMENTOS DE FATURA
   ============================================================ */

faturas AS (

  SELECT
    id_usuario,
    data_movimento AS data_pagto,
    EXTRACT(DAY FROM data_movimento) AS dia_mes

  FROM transacoes

  WHERE nom_cate_micro = 'Pagamento de fatura'
),

saidas_todas AS (

  SELECT
    id_usuario,
    EXTRACT(DAY FROM data_movimento) AS dia_mes

  FROM transacoes

  WHERE UPPER(tipo) = 'S'
),

/* ============================================================
   3. DIA ESTIMADO DE VENCIMENTO

   Preferência:
   1. moda dos dias de pagamento da fatura
   2. fallback: moda geral dos dias de saída
   ============================================================ */

moda_vencimento AS (

  SELECT
    id_usuario,
    dia_mes,
    COUNT(*) AS qtd

  FROM faturas

  GROUP BY
    id_usuario,
    dia_mes

  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY id_usuario
    ORDER BY qtd DESC, dia_mes ASC
  ) = 1
),

moda_saida_geral AS (

  SELECT
    id_usuario,
    dia_mes,
    COUNT(*) AS qtd

  FROM saidas_todas

  GROUP BY
    id_usuario,
    dia_mes

  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY id_usuario
    ORDER BY qtd DESC, dia_mes ASC
  ) = 1
),

dia_vencimento_cliente AS (

  SELECT
    c.id_usuario,

    COALESCE(
      mv.dia_mes,
      ms.dia_mes,
      1
    ) AS dia_vencimento

  FROM clientes c

  LEFT JOIN moda_vencimento mv
    USING (id_usuario)

  LEFT JOIN moda_saida_geral ms
    USING (id_usuario)
),

/* ============================================================
   4. TRÊS CICLOS MAIS RECENTES
   ============================================================ */

pagamentos_recentes AS (

  SELECT
    id_usuario,
    data_pagto,

    ROW_NUMBER() OVER (
      PARTITION BY id_usuario
      ORDER BY data_pagto DESC
    ) AS rn_desc

  FROM faturas
),

ciclos_pivot AS (

  SELECT
    id_usuario,

    MAX(
      IF(rn_desc = 3, data_pagto, NULL)
    ) AS venc_1,

    MAX(
      IF(rn_desc = 2, data_pagto, NULL)
    ) AS venc_2,

    MAX(
      IF(rn_desc = 1, data_pagto, NULL)
    ) AS venc_3

  FROM pagamentos_recentes

  WHERE rn_desc <= 3

  GROUP BY id_usuario
),

ciclos_definicao AS (

  /* Ciclo 1 começa artificialmente no início do D-90.
     Ele NÃO será usado para definir recorrência. */

  SELECT
    cp.id_usuario,
    1 AS ciclo,
    j.inicio_janela AS data_inicio,
    cp.venc_1 AS data_fim,
    TRUE AS ciclo_parcial

  FROM ciclos_pivot cp

  CROSS JOIN janela j

  WHERE cp.venc_1 IS NOT NULL

  UNION ALL

  SELECT
    id_usuario,
    2 AS ciclo,
    venc_1 AS data_inicio,
    venc_2 AS data_fim,
    FALSE AS ciclo_parcial

  FROM ciclos_pivot

  WHERE venc_1 IS NOT NULL
    AND venc_2 IS NOT NULL

  UNION ALL

  SELECT
    id_usuario,
    3 AS ciclo,
    venc_2 AS data_inicio,
    venc_3 AS data_fim,
    FALSE AS ciclo_parcial

  FROM ciclos_pivot

  WHERE venc_2 IS NOT NULL
    AND venc_3 IS NOT NULL
),

/* ============================================================
   5. FLUXO OBSERVADO POR CICLO

   caixa gerado =
       entradas recorrentes
       - saídas não-cartão

   Excluímos:
   - 13º salário
   - pagamento da própria fatura
   - compras no cartão

   IMPORTANTE:
   isto NÃO representa saldo bancário.
   ============================================================ */

fluxos_ciclo AS (

  SELECT
    cd.id_usuario,
    cd.ciclo,
    cd.data_inicio,
    cd.data_fim,
    cd.ciclo_parcial,

    SUM(
      CASE

        WHEN UPPER(t.tipo) = 'E'
         AND NOT COALESCE(t.flag_13_salario, FALSE)

        THEN t.vlr

        ELSE 0

      END
    ) AS entradas_recorrentes,

    SUM(
      CASE

        WHEN UPPER(t.tipo) = 'S'

         AND IFNULL(
           t.nom_cate_micro,
           ''
         ) != 'Pagamento de fatura'

         AND NOT (
           LOWER(COALESCE(t.descr, '')) LIKE '%cart credito%'
           AND LOWER(COALESCE(t.descr, '')) NOT LIKE '%pag%fat%'
         )

        THEN t.vlr

        ELSE 0

      END
    ) AS saidas_nao_cartao

  FROM ciclos_definicao cd

  LEFT JOIN transacoes t
    ON t.id_usuario = cd.id_usuario
   AND t.data_movimento > cd.data_inicio
   AND t.data_movimento <= cd.data_fim

  GROUP BY
    cd.id_usuario,
    cd.ciclo,
    cd.data_inicio,
    cd.data_fim,
    cd.ciclo_parcial
),

/* ============================================================
   6. CAUDA HISTÓRICA

   Usa os últimos 5 dias dos ciclos anteriores para estimar
   o comportamento entre D-5 e o vencimento do ciclo atual.
   ============================================================ */

cauda_historica AS (

  SELECT
    cd.id_usuario,

    SUM(
      CASE

        WHEN UPPER(t.tipo) = 'E'
         AND NOT COALESCE(t.flag_13_salario, FALSE)

        THEN t.vlr

        ELSE 0

      END
    ) AS entradas_cauda,

    SUM(
      CASE

        WHEN UPPER(t.tipo) = 'S'

         AND IFNULL(
           t.nom_cate_micro,
           ''
         ) != 'Pagamento de fatura'

         AND NOT (
           LOWER(COALESCE(t.descr, '')) LIKE '%cart credito%'
           AND LOWER(COALESCE(t.descr, '')) NOT LIKE '%pag%fat%'
         )

        THEN t.vlr

        ELSE 0

      END
    ) AS saidas_cauda,

    COUNT(
      DISTINCT cd.ciclo
    ) AS ciclos_com_cauda

  FROM ciclos_definicao cd

  LEFT JOIN transacoes t
    ON t.id_usuario = cd.id_usuario
   AND t.data_movimento > DATE_SUB(
         cd.data_fim,
         INTERVAL 5 DAY
       )
   AND t.data_movimento <= cd.data_fim

  WHERE cd.ciclo IN (1, 2)

  GROUP BY cd.id_usuario
),

/* ============================================================
   7. CICLO ATUAL OBSERVADO ATÉ D-5
   ============================================================ */

ciclo_atual_parcial AS (

  SELECT
    cd.id_usuario,

    DATE_SUB(
      cd.data_fim,
      INTERVAL 5 DAY
    ) AS data_simulada,

    SUM(
      CASE

        WHEN UPPER(t.tipo) = 'E'
         AND NOT COALESCE(t.flag_13_salario, FALSE)

        THEN t.vlr

        ELSE 0

      END
    ) AS entradas_parcial,

    SUM(
      CASE

        WHEN UPPER(t.tipo) = 'S'

         AND IFNULL(
           t.nom_cate_micro,
           ''
         ) != 'Pagamento de fatura'

         AND NOT (
           LOWER(COALESCE(t.descr, '')) LIKE '%cart credito%'
           AND LOWER(COALESCE(t.descr, '')) NOT LIKE '%pag%fat%'
         )

        THEN t.vlr

        ELSE 0

      END
    ) AS saidas_parcial

  FROM ciclos_definicao cd

  LEFT JOIN transacoes t
    ON t.id_usuario = cd.id_usuario
   AND t.data_movimento > cd.data_inicio
   AND t.data_movimento <= DATE_SUB(
         cd.data_fim,
         INTERVAL 5 DAY
       )

  WHERE cd.ciclo = 3

  GROUP BY
    cd.id_usuario,
    cd.data_fim
),

/* ============================================================
   8. CAIXA DISPONÍVEL ESTIMADO

   NÃO existe carry-over.

   Cada ciclo é independente.

   Ciclo 3:
   observado até D-5 + média da cauda histórica.
   ============================================================ */

capacidade_ciclo AS (

  SELECT
    fc.id_usuario,
    fc.ciclo,
    fc.data_inicio,
    fc.data_fim,
    fc.ciclo_parcial,

    CASE

      WHEN fc.ciclo = 3 THEN

        (
          COALESCE(
            cap.entradas_parcial,
            0
          )

          +

          COALESCE(
            SAFE_DIVIDE(
              ch.entradas_cauda,
              NULLIF(
                ch.ciclos_com_cauda,
                0
              )
            ),
            0
          )
        )

        -

        (
          COALESCE(
            cap.saidas_parcial,
            0
          )

          +

          COALESCE(
            SAFE_DIVIDE(
              ch.saidas_cauda,
              NULLIF(
                ch.ciclos_com_cauda,
                0
              )
            ),
            0
          )
        )

      ELSE

        fc.entradas_recorrentes
        -
        fc.saidas_nao_cartao

    END AS caixa_disponivel_estimado

  FROM fluxos_ciclo fc

  LEFT JOIN ciclo_atual_parcial cap
    USING (id_usuario)

  LEFT JOIN cauda_historica ch
    USING (id_usuario)
),

/* ============================================================
   9. FATURA + PRESSÃO DE CAIXA

   Três conceitos diferentes:

   deficit_pre_fatura
     = déficit que já existiria antes da fatura.

   valor_faltante_fatura
     = parcela da fatura sem cobertura.
     Nunca pode ser maior que a própria fatura.

   deficit_total_ciclo
     = pressão financeira total considerando
       déficit prévio + fatura.

   Essa separação é importante para o agente não dizer,
   por exemplo, que "faltam R$8 mil para uma fatura de R$1,5 mil".
   ============================================================ */

fatura_por_ciclo AS (

  SELECT
    cc.id_usuario,
    cc.ciclo,
    cc.data_inicio,
    cc.data_fim,
    cc.ciclo_parcial,

    cc.caixa_disponivel_estimado,

    COALESCE(
      sc.fatura_estimada,
      0
    ) AS fatura_estimada,

    /* Déficit existente antes de considerar a fatura */

    GREATEST(
      -cc.caixa_disponivel_estimado,
      0
    ) AS deficit_pre_fatura,

    /* Parte da própria fatura que não possui cobertura */

    GREATEST(
      COALESCE(
        sc.fatura_estimada,
        0
      )
      -
      GREATEST(
        cc.caixa_disponivel_estimado,
        0
      ),
      0
    ) AS valor_faltante_fatura,

    /* Pressão total de caixa */

    GREATEST(
      COALESCE(
        sc.fatura_estimada,
        0
      )
      -
      cc.caixa_disponivel_estimado,
      0
    ) AS deficit_total_ciclo

  FROM capacidade_ciclo cc

  LEFT JOIN
    `batalha-time-05-xew3.hackathon_dados.silver_cartao` sc

    ON sc.id_usuario = cc.id_usuario

   AND sc.anomes =
       CAST(
         FORMAT_DATE(
           '%Y%m',
           cc.data_fim
         )
         AS INT64
       )
),

/* ============================================================
   10. RESUMO DOS CICLOS

   A classificação considera falta de cobertura da FATURA.

   Ciclo 1:
     ignorado para recorrência por ser parcial.

   Ciclo 3:
     situação atual.

   Regras:
     ciclo 3 sem falta             -> SEM_FALTA
     ciclo 2 + ciclo 3 com falta  -> RECORRENTE
     somente ciclo 3 com falta    -> PONTUAL
   ============================================================ */

resumo_ciclos AS (

  SELECT
    id_usuario,

    MAX(
      CASE

        WHEN ciclo = 2
         AND valor_faltante_fatura > 0

        THEN 1

        ELSE 0

      END
    ) AS falta_ciclo_2,

    MAX(
      CASE

        WHEN ciclo = 3
         AND valor_faltante_fatura > 0

        THEN 1

        ELSE 0

      END
    ) AS falta_ciclo_3,

    MAX(
      CASE
        WHEN ciclo = 3
        THEN caixa_disponivel_estimado
      END
    ) AS caixa_atual,

    MAX(
      CASE
        WHEN ciclo = 3
        THEN fatura_estimada
      END
    ) AS fatura_atual,

    MAX(
      CASE
        WHEN ciclo = 3
        THEN deficit_pre_fatura
      END
    ) AS deficit_pre_fatura_atual,

    MAX(
      CASE
        WHEN ciclo = 3
        THEN valor_faltante_fatura
      END
    ) AS falta_fatura_atual,

    MAX(
      CASE
        WHEN ciclo = 3
        THEN deficit_total_ciclo
      END
    ) AS deficit_total_atual

  FROM fatura_por_ciclo

  GROUP BY id_usuario
)

/* ============================================================
   11. OUTPUT FINAL
   ============================================================ */

SELECT
  c.id_usuario,

  dv.dia_vencimento,

  COALESCE(
    cap.data_simulada,

    DATE_SUB(
      j.fim_janela,
      INTERVAL 5 DAY
    )
  ) AS data_simulada,

  /*
    Capacidade líquida estimada gerada no ciclo.
    NÃO representa saldo bancário.
  */

  COALESCE(
    rc.caixa_atual,
    0
  ) AS caixa_disponivel_estimado,

  COALESCE(
    rc.fatura_atual,
    0
  ) AS fatura_estimada,

  /*
    Déficit que já existiria antes da cobrança da fatura.
  */

  COALESCE(
    rc.deficit_pre_fatura_atual,
    0
  ) AS deficit_pre_fatura,

  /*
    Quanto da própria fatura não possui cobertura.

    Este é o campo adequado para frases como:
    "estimamos que faltarão R$ X para quitar sua fatura".
  */

  COALESCE(
    rc.falta_fatura_atual,
    0
  ) AS valor_faltante_fatura,

  /*
    Pressão financeira total do ciclo.

    Uso analítico / motor.
    Não deve ser apresentado ao cliente como
    "valor que falta para pagar a fatura".
  */

  COALESCE(
    rc.deficit_total_atual,
    0
  ) AS deficit_total_ciclo,

  CASE

    WHEN COALESCE(
      rc.falta_ciclo_3,
      0
    ) = 0
      THEN 'SEM_FALTA'

    WHEN COALESCE(
      rc.falta_ciclo_2,
      0
    ) = 1
     AND COALESCE(
       rc.falta_ciclo_3,
       0
     ) = 1
      THEN 'RECORRENTE'

    ELSE 'PONTUAL'

  END AS tipo_falta

FROM clientes c

CROSS JOIN janela j

LEFT JOIN dia_vencimento_cliente dv
  USING (id_usuario)

LEFT JOIN resumo_ciclos rc
  USING (id_usuario)

LEFT JOIN ciclo_atual_parcial cap
  USING (id_usuario);
