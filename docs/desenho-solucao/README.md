# Desenho de solução (entregável 4)

Arquitetura do Cabe no Bolso em uma página, com as três lentes pedidas: arquitetura, engenharia e ciência de dados. O detalhamento dos componentes e das decisões está em `docs/04-arquitetura-gcp.md`.

| Arquivo | Uso |
|---|---|
| `desenho-solucao-claro.png` | Formulário de entrega e impressão (5160×2880, 16:9) |
| `desenho-solucao-escuro.png` | Slide do pitch em fundo escuro (5160×2880, 16:9) |
| `desenho-solucao.svg` | Vetor editável, com tema claro e escuro |
| `arquitetura.html` | Versão navegável: zoom, busca e vistas guiadas por lente (Arquitetura, Engenharia, Ciência de dados) |
| `arquitetura.json` | Fonte versionada do diagrama |

## Como ler

- **Caminho principal:** Demo web → API FastAPI → Agente ADK → Checagens + validador. Nenhuma resposta chega ao cliente sem passar pelas checagens em código e pelo validador.
- **Números:** a API pede os números ao `cabe_core` (motor determinístico), que lê o extrato do CSV de reserva (padrão da demo) ou do BigQuery (`DADOS=bigquery`) e as taxas de `config/taxas.yaml`. O Gemini só escreve o texto e valida o tom.
- **Contorno tracejado vermelho:** o que roda dentro do serviço Cloud Run `cabe-no-bolso`, com a identidade `squad-agent-sa` e sem chaves.
- **Setas tracejadas:** caminhos opcionais. `DADOS=bigquery` troca o CSV pelo BigQuery. O runtime alternativo (Agent Engine + MCP, em `agents/cabe` e `mcp_server/`) lê a gold do Lucas direto no BigQuery e não é o caminho da demo.

## Ferramenta e validação

Gerado com [archify](https://github.com/tt-a1i/archify) v2.17 a partir de `arquitetura.json`. Validação do perfil `showcase` em 27/09/2026:

- 9/9 checagens do artefato, 0 erros e 0 avisos de composição;
- checagem visual em Chrome sem estouro de tela em 1440×900, 1600×1000, 1920×1080 e 2048×1320.

Os botões do visualizador ficam em inglês porque o archify não tem tradução para pt-BR; o conteúdo do diagrama está em português.

Para regenerar depois de editar o JSON:

```bash
cd ~/.claude/skills/archify
node bin/archify.mjs deliver architecture <repo>/docs/desenho-solucao/arquitetura.json <repo>/docs/desenho-solucao/arquitetura.html --quality showcase --json
```

Os PNG e o SVG saem do menu Export do `arquitetura.html`.
