"""server: FastAPI que serve a API (/api/...) e a demo estática (/) num único serviço (Cloud Run).

Módulos: main (app e rotas), sessoes (estado em memória com TTL), conversa (modo sem LLM e ponte para
cabe_no_bolso.runtime), guardiao (confere números e termos na borda), limites (rate limit, corpo, concorrência,
origem e cabeçalhos de segurança).
"""
