"""API do Cabe no Bolso (FastAPI) + demo estática no mesmo serviço (Cloud Run, um worker, sessões em memória).

Rotas (prefixo /api): saude, personas, sessao, consentimento, mensagem, avancar-mes, trace/{id}, painel/{id}.
A demo (demo/ na raiz do repo) é servida em /. Erros saem sempre como {erro, mensagem_cliente}.

Dois modos por sessão, decididos em código (server/conversa.py):
- llm: cabe_no_bolso.runtime (agente ADK + Gemini) é dono da sessão; esta camada valida entrada, aplica limites,
  passa o guardião de novo na borda e espelha o mínimo de estado para a reserva;
- sem_llm: resposta determinística montada só com cabe_core (runtime ausente, MODO_CONVERSA=sem_llm ou falha do
  modelo no meio da sessão: a sessão degrada sem erro visível para a banca).
/api/avancar-mes, /api/trace e /api/painel nunca chamam o modelo em nenhum dos modos.

Rodar (a partir de agent/): uv run uvicorn server.main:app --port 8080
Variáveis: DADOS=csv|bigquery, MODELO, MODO_CONVERSA=auto|sem_llm, RAIZ, TAXAS, SESSAO_TTL_S, RATE_LIMIT_POR_MINUTO,
MAX_CONCORRENCIA, MAX_CORPO_BYTES, GOOGLE_* (Vertex, só usadas pelo runtime do agente).
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from cabe_core import calendario, capacidade, config, dados
from cabe_no_bolso import policy

from . import conversa
from .limites import Limites
from .sessoes import Sessoes, agora_iso, novo_estado

log = logging.getLogger("cabe_no_bolso.server")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # o cliente HTTP do runtime loga URLs; só avisos

MODELO = os.environ.get("MODELO", "gemini-3.8-flash")
ERRO_ENTRADA = "Não entendi o pedido. Tente de novo."
ERRO_SESSAO = "Sua sessão expirou. Recarregue a página para começar de novo."
UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
SESSAO_RE = r"^[A-Za-z0-9_-]+$"


# ----------------------------------------------------------------------------- entrada (validação forte; entrada é dado)
class SessaoIn(BaseModel):
    cliente_id: str = Field(pattern=UUID_RE)
    anomes: int = Field(ge=200001, le=209912)
    persona: str | None = Field(default=None, max_length=20, pattern=r"^[a-z0-9_-]*$")
    contar_pix: bool | None = None


class ConsentimentoIn(BaseModel):
    sessao_id: str = Field(min_length=8, max_length=64, pattern=SESSAO_RE)
    concedido: bool


class MensagemIn(BaseModel):
    sessao_id: str = Field(min_length=8, max_length=64, pattern=SESSAO_RE)
    texto: str | None = Field(default=None, max_length=500)
    acao: Literal[conversa.ACOES] | None = None  # type: ignore[valid-type]
    valor: int | None = Field(default=None, ge=1, le=100_000_000)


class SessaoRef(BaseModel):
    sessao_id: str = Field(min_length=8, max_length=64, pattern=SESSAO_RE)


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse({"erro": codigo, "mensagem_cliente": mensagem}, status_code=status)


# ----------------------------------------------------------------------------- app
def criar_app(*, req_por_minuto: int | None = None, max_concorrencia: int | None = None, max_corpo: int | None = None,
              ttl_s: int | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        t0 = time.perf_counter()
        app.state.taxas = config.carregar_taxas()
        app.state.fonte = dados.fonte_padrao()
        if hasattr(app.state.fonte, "caminho"):
            await run_in_threadpool(dados._carregar_csv, app.state.fonte.caminho)  # aquece o cache antes da primeira sessão
        app.state.sessoes = Sessoes(ttl_s=ttl_s or int(os.environ.get("SESSAO_TTL_S", "7200")))
        log.info("pronto: dados=%s modo=%s modelo=%s demo=%s em %.1fs", app.state.fonte.nome, conversa.modo_atual(), MODELO,
                 DEMO.exists(), time.perf_counter() - t0)
        yield

    app = FastAPI(title="Cabe no Bolso · API", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        Limites,
        req_por_minuto=req_por_minuto or int(os.environ.get("RATE_LIMIT_POR_MINUTO", "60")),
        max_concorrencia=max_concorrencia or int(os.environ.get("MAX_CONCORRENCIA", "40")),
        max_corpo=max_corpo or int(os.environ.get("MAX_CORPO_BYTES", str(16 * 1024))),
    )

    # ------------------------------------------------------------------ erros: sempre {erro, mensagem_cliente}, sem PII
    @app.exception_handler(RequestValidationError)
    async def _invalido(_: Request, exc: RequestValidationError):
        return erro(422, "entrada_invalida", ERRO_ENTRADA)

    @app.exception_handler(capacidade.DadosIndisponiveis)
    async def _sem_dados(_: Request, exc: capacidade.DadosIndisponiveis):
        return erro(404, "dados_indisponiveis", conversa.ERRO_DADOS)

    @app.exception_handler(conversa.SemConsentimento)
    async def _sem_consentimento(_: Request, exc: conversa.SemConsentimento):
        return erro(403, "sem_consentimento", conversa.TEXTO_SEM_CONSENTIMENTO)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return erro(404, "nao_encontrado", "Não encontrei o que você pediu.")
        return erro(exc.status_code, f"http_{exc.status_code}", str(exc.detail) if exc.detail else ERRO_ENTRADA)

    @app.exception_handler(Exception)
    async def _interno(_: Request, exc: Exception):
        log.error("erro interno: %s", type(exc).__name__)
        return erro(500, "interno", conversa.ERRO_DADOS)

    # ------------------------------------------------------------------ utilidades das rotas
    def sessao_de(request: Request, sessao_id: str) -> dict | None:
        return request.app.state.sessoes.obter(sessao_id)

    def runtime_de(estado: dict):
        """Módulo do agente quando a sessão está no modo llm; None manda a rota para a reserva sem LLM."""
        return conversa.carregar_runtime() if estado.get("modo") == "llm" else None

    # ------------------------------------------------------------------ rotas
    @app.get("/api/saude")
    async def saude(request: Request):
        modo = conversa.modo_atual()
        return {"ok": True, "dados": request.app.state.fonte.nome, "modelo": MODELO if modo == "llm" else None, "modo": modo,
                "sessoes": len(request.app.state.sessoes), "versao": app.version}

    @app.get("/api/personas")
    async def personas(request: Request):
        rot = (request.app.state.taxas.get("grupos") or {}).get("rotulos") or {}
        return [{"cliente_id": p["id"], "apelido": p.get("apelido"), "anomes": p.get("anomes"), "perfil": p.get("perfil"),
                 "grupo_rotulo": rot.get(p.get("grupo_esperado"), p.get("grupo_esperado"))} for p in policy.personas_demo(request.app.state.taxas)]

    @app.post("/api/sessao")
    async def sessao(entrada: SessaoIn, request: Request):
        st = request.app.state
        if not calendario.valido(entrada.anomes):
            return erro(422, "entrada_invalida", ERRO_ENTRADA)
        existe = await run_in_threadpool(st.fonte.existe, entrada.cliente_id)
        if not existe:
            return erro(404, "cliente_nao_encontrado", conversa.ERRO_DADOS)
        p = policy.persona_demo(entrada.cliente_id, st.taxas)
        rot = (st.taxas.get("grupos") or {}).get("rotulos") or {}
        estado = novo_estado(entrada.cliente_id, entrada.anomes, entrada.persona, (p or {}).get("apelido") or "cliente",
                             (p or {}).get("perfil") or "", conversa.modo_atual())
        estado["grupo_rotulo_config"] = rot.get((p or {}).get("grupo_esperado")) if p else None
        estado["contar_pix"] = entrada.contar_pix
        sid = st.sessoes.criar(estado)
        try:
            resposta = await run_in_threadpool(conversa.abrir_sessao, estado, st.fonte, st.taxas)
            mod = runtime_de(estado)
            if mod is not None:
                await conversa.rt_sessao(estado, mod)
        except Exception:
            st.sessoes.remover(sid)
            raise
        resposta["modo"] = estado["modo"]
        resposta["numeros_validados"] = [dict(n) for n in estado["numeros_validados"]]
        log.info("sessao criada sessao=%s cliente=%s… anomes=%s modo=%s", sid[:6], entrada.cliente_id[:8], entrada.anomes, estado["modo"])
        return resposta

    @app.post("/api/consentimento")
    async def consentimento(entrada: ConsentimentoIn, request: Request):
        st = request.app.state
        estado = sessao_de(request, entrada.sessao_id)
        if estado is None:
            return erro(404, "sessao_nao_encontrada", ERRO_SESSAO)
        async with st.sessoes.lock(entrada.sessao_id):
            r = None
            mod = runtime_de(estado)
            if mod is not None:
                r = await conversa.rt_consentir(estado, mod, entrada.concedido)
            if r is None:
                r = await run_in_threadpool(conversa.registrar_consentimento, estado, st.fonte, st.taxas, entrada.concedido, agora_iso())
                r["modo"] = estado["modo"]
        log.info("consentimento sessao=%s concedido=%s modo=%s", entrada.sessao_id[:6], entrada.concedido, estado["modo"])
        return r

    @app.post("/api/mensagem")
    async def mensagem(entrada: MensagemIn, request: Request):
        st = request.app.state
        if entrada.acao is None and not (entrada.texto or "").strip():
            return erro(422, "entrada_invalida", ERRO_ENTRADA)
        if entrada.acao == "pagar_outro_valor" and entrada.valor is None:
            return erro(422, "valor_obrigatorio", "Digite um valor.")
        estado = sessao_de(request, entrada.sessao_id)
        if estado is None:
            return erro(404, "sessao_nao_encontrada", ERRO_SESSAO)
        texto = (entrada.texto or "").strip() or None
        t0 = time.perf_counter()
        async with st.sessoes.lock(entrada.sessao_id):
            r = None
            mod = runtime_de(estado)
            if mod is not None and estado.get("consentimento") is True:  # sem consentimento nem o agente entra: nenhum dado lido
                r = await conversa.rt_mensagem(estado, mod, acao=entrada.acao, texto=texto, valor=entrada.valor)
            if r is None:
                r = await conversa.conversar(estado, st.fonte, st.taxas, acao=entrada.acao, texto=texto, valor=entrada.valor)
        g = r.get("guardiao") or {}
        log.info("mensagem sessao=%s acao=%s modo=%s cards=%d removidos=%d termos=%d ms=%.0f", entrada.sessao_id[:6], entrada.acao or "texto",
                 r.get("modo"), len(r.get("cards") or []), len(g.get("removidos") or []), len(g.get("termos_bloqueados") or []),
                 (time.perf_counter() - t0) * 1000)
        return r

    @app.post("/api/avancar-mes")
    async def avancar_mes(entrada: SessaoRef, request: Request):
        st = request.app.state
        estado = sessao_de(request, entrada.sessao_id)
        if estado is None:
            return erro(404, "sessao_nao_encontrada", ERRO_SESSAO)
        async with st.sessoes.lock(entrada.sessao_id):
            if estado.get("consentimento") is not True:
                return erro(403, "sem_consentimento", conversa.TEXTO_SEM_CONSENTIMENTO)
            r = None
            mod = runtime_de(estado)
            if mod is not None:
                r = await conversa.rt_avancar(estado, mod)
                if r is not None and r.get("erro") == "sem_plano":
                    return erro(409, "sem_plano", "Confirme uma opção no ia.i antes de avançar o mês.")
                if r is not None and r.get("erro") == "plano_encerrado":
                    return erro(409, "plano_encerrado", "O plano já encerrou: três faturas inteiras seguidas.")
            if r is None:
                try:
                    r = await run_in_threadpool(conversa.avancar_mes, estado, st.fonte, st.taxas)
                except ValueError as e:
                    if str(e) == "sem_plano":
                        return erro(409, "sem_plano", "Confirme uma opção no ia.i antes de avançar o mês.")
                    if str(e) == "plano_encerrado":
                        return erro(409, "plano_encerrado", "O plano já encerrou: três faturas inteiras seguidas.")
                    raise
        log.info("avancar-mes sessao=%s anomes=%s ok=%s encerrado=%s modo=%s", entrada.sessao_id[:6], r["anomes"], r["ciclos_ok"], r["encerrado"], r.get("modo"))
        return r

    @app.get("/api/trace/{sessao_id}")
    async def trace(sessao_id: str, request: Request):
        estado = sessao_de(request, sessao_id)
        if estado is None:
            return erro(404, "sessao_nao_encontrada", ERRO_SESSAO)
        mod = runtime_de(estado)
        if mod is not None:
            r = await conversa.rt_trace(estado, mod)
            if r is not None:
                return r
        return [dict(t) for t in estado["trace"]]

    @app.get("/api/painel/{sessao_id}")
    async def painel(sessao_id: str, request: Request):
        st = request.app.state
        estado = sessao_de(request, sessao_id)
        if estado is None:
            return erro(404, "sessao_nao_encontrada", ERRO_SESSAO)
        async with st.sessoes.lock(sessao_id):
            mod = runtime_de(estado)
            if mod is not None:
                r = await conversa.rt_painel(estado, mod, st.taxas, MODELO)
                if r is not None:
                    return r
            return await run_in_threadpool(conversa.painel_juri, estado, st.fonte, st.taxas, MODELO)

    # ------------------------------------------------------------------ demo estática em / (depois das rotas da API)
    if DEMO.is_dir():
        app.mount("/", StaticFiles(directory=str(DEMO), html=True), name="demo")
    else:  # pragma: no cover
        log.warning("pasta da demo não encontrada em %s; só a API está disponível", DEMO)
    return app


DEMO = config.raiz() / "demo"
app = criar_app()
