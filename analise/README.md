# analise/

Scripts exploratórios que geraram os números de `docs/01-evidencias-base.md`. Todos leem `data/extrato_sintetico.csv.gz` com pandas e imprimem no terminal. Rodar com `python3 nome.py` a partir desta pasta (ajustar `P` se o repo mudar de lugar). Não são código de produto; o núcleo do agente vive em `agent/cabe_core`.

| Script | O que responde |
|---|---|
| `verifica.py` | Modos de pagamento, pagadores parciais, juros no mesmo mês, fatura x compras do cartão |
| `persona.py` | Candidatos a persona e mês a mês de um cliente CLT com financiamento |
| `persona_b.py` | Escolhe a persona do grupo B e exporta `data/personas/*.json` |
| `extra.py` | Entradas/saídas recorrentes da persona; teste "sobra do mês" parcial x integral; texto do PPTX |
| `ideias.py` | Testa ideias da reunião (parcelas como sinal, oficina, cashback) |
| `impacto.py` | Juros evitáveis com e sem PIX; renda extra; caixa anual; renegociação |
| `arquetipos.py` | Perfis por renda e moradia; taxa de pagamento parcial por flag |
| `alavancas.py` | Persistência mês a mês; vencimento x renda; peso por perfil; correlações |
| `solucao_dados.py` | Correlações dentro dos perfis; persona CLT sem financiamento |
| `varredura.py` | Varredura de todos os sinais dos três pilares por perfil (checagem de viés) |
| `juros.py`, `juros2.py` | Decodificação dos juros: rotativo 14%, cheque especial, reconstrução da fatura, sequências |
| `grupos_abc.py` | Grupos A/B/C, juros por grupo, base não carrega saldo |
| `fill.py`, `nome_cabe_no_bolso.py` | Preenchimento do PPTX oficial (fichas) |
