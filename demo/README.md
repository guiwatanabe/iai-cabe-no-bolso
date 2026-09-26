# demo/ · especificação da demo web (a construir)

Objetivo: a banca abre pelo QR no celular e percorre a jornada de `docs/07-demo-roteiro.md` em menos de 3 minutos, enquanto o time apresenta.

## Requisitos

- Mobile-first (≈390px), uma página, sem login. Servida pelo Cloud Run junto com a API (`/`), ou estática com respostas gravadas como plano B.
- Telas/estados: Pagar fatura → painel do agente (sinal) → consentimento → diagnóstico → pergunta → comparador de saídas → confirmação → linha do tempo (avançar mês ×3) → encerramento → painel "como cheguei aqui".
- Componentes e tokens: `docs/06-design-system-ai.md`. Sem marca do Itaú salvo brandbook oficial.
- Rodapé fixo: "Protótipo do Time 05. Data, taxas, elegibilidade e meses seguintes são simulados. Nenhum pagamento ou contratação real."
- Cada número exibido carrega `data-origem` (nome da ferramenta) para o painel da banca.
- Botão "reiniciar" discreto para a próxima pessoa da banca.

## Dados da demo

- Persona: `data/personas/3e7d20b2_grupo_b.json` (real). Data simulada: 20/02/2025.
- Taxas: `config/taxas.yaml`.
- Meses seguintes: gerados por `cabe_core.acompanhar` a partir do plano confirmado (simulação declarada).

## API esperada

`POST /sessao {cliente_id, mes}` → `{sessao_id, tela_fatura}` · `POST /mensagem {sessao_id, texto|acao}` → `{mensagens[], cards[], numeros_validados[]}` · `POST /avancar-mes {sessao_id}` → estado do plano · `GET /trace/{sessao_id}` → lista de chamadas de ferramenta.

## QR code

Gerar a partir da URL final do Cloud Run (`qrcode` em Python ou qualquer gerador); colocar no slide 3 e em um cartão impresso na mesa.
