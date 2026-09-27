# 08 · Plano de execução (26 → 27/09)

Ordem por dependência e por risco. Cada item tem dono (a preencher pelo time), evidência de pronto e o que cortar se apertar.

| # | Entrega | Dono | Pronto quando | Corte se apertar |
|---|---|---|---|---|
| 1 | Núcleo determinístico `cabe_core` + testes com o CSV | | `pytest` verde; funções reproduzem os números de `01-evidencias-base.md` para a persona | Sem `acompanhar`; simular meses seguintes com regra fixa |
| 2 | `config/taxas.yaml` conferido | | Cada taxa com fonte e data; time de acordo | Manter só rotativo (base), parcelamento de fatura (BC) e crédito pessoal |
| 3 | Agente ADK local (Dev UI) | | Jornada da persona completa na Dev UI; golden set 10/10 | Sem pergunta aberta; só sim/não |
| 4 | Demo web mobile-first | | QR abre no celular; jornada dos passos 1–9 do roteiro; rodapé de simulação | Sem "avançar mês" animado: três cartões estáticos |
| 5 | Deploy no Cloud Run + QR | | URL HTTPS pública respondendo; `max-instances=5` | Túnel local + vídeo de 60s |
| 6 | Pitch (5 slides) | | Ensaio em 3 minutos cronometrado | Slide 4 vira uma frase |
| 7 | Entregáveis: proposta, racional, desenho, arquitetura | | `docs/02`, `03`, `04` revisados pelo time; desenho exportado | Usar os docs como estão |
| 8 | Golden set de Responsible AI | | 10 casos de `05-responsible-ai.md` passam | 6 casos essenciais (1, 2, 5, 6, 8, 9) |

## Ordem recomendada

Manhã: 1 → 2 → 3 (em paralelo: 6 e 7 por quem não codifica). Depois: 4 → 5 → 8. Ensaio do pitch com a demo no celular às 13h; ajustes; ensaio final antes de apresentar.

## Riscos e resposta

- **Modelo/plataforma indisponível:** demo com respostas gravadas do agente (mesmo texto da Dev UI), marcadas como gravadas.
- **Orçamento GCP:** BigQuery só por views agregadas; testes no CSV; Gemini flash; sem Antigravity em loop.
- **Discurso desalinhado no time:** ler `02-proposta-produto.md` juntos; divergência registrada em `decisoes.md` antes de codificar.
- **Tempo do pitch:** cortar do slide 4 primeiro; a demo fala pelo produto.

## Definição de pronto da demo

1. A banca abre pelo QR, escolhe pagar o mínimo, dá consentimento, vê causa, vê duas saídas com custo, confirma, avança três meses e abre "como cheguei aqui".
2. Nenhum número na tela sem origem em ferramenta.
3. Rodapé de simulação visível em todas as telas.
4. Funciona offline do BigQuery (CSV) se preciso.
