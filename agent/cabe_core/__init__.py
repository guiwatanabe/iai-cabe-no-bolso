"""cabe_core: núcleo determinístico do Cabe no Bolso. Funções puras, sem rede, sem LLM.

Dinheiro em centavos (int); datas como anomes (AAAAMM); cada retorno traz 'origem' por número.

Módulos: dados (CSV/BigQuery), fatura (reconstrução), capacidade (motor de 90 dias), grupo (escorregão/rolando/no limite),
anomalia (renda irregular, gastos atípicos), ofertas (caminho e opções), travas (policy pura), acompanhar (ciclos sem LLM),
painel (como cheguei aqui), dinheiro (centavos, R$, origem), calendario (anomes), config (taxas.yaml).
"""
from . import acompanhar, anomalia, calendario, capacidade, config, dados, dinheiro, fatura, grupo, ofertas, painel, travas

__all__ = ["acompanhar", "anomalia", "calendario", "capacidade", "config", "dados", "dinheiro", "fatura", "grupo", "ofertas",
           "painel", "travas"]
