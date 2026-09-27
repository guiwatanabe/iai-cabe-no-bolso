"""Demo API in front of the `cabe` agent on Agent Engine; owns identity (uid cookie, cliente_id allowlist)."""

import json
import os
import re
import uuid
from functools import cache
from typing import Annotated, Any, Literal

import vertexai
from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from google.genai import errors
from pydantic import BaseModel

# cliente_id -> primeiro_nome. Real personas come from gold once BigQuery runs; the fixture ids only
# resolve when the agent runs with CABE_DADOS=fixtures.
CLIENTES_DEMO = {
    "3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b": "",
    "fixture-escorregao": "Ana",
    "fixture-rolando": "Bruno",
    "fixture-no-limite": "Carla",
}
SESSION_TTL_S = 86400
_UID = re.compile(r"[0-9a-f]{32}")

app = FastAPI(title="cabe-no-bolso")

Uid = Annotated[str | None, Cookie()]


@cache
def engine():
    name = os.environ["AGENT_ENGINE"]  # projects/{project}/locations/{location}/reasoningEngines/{id}
    _, project, _, location, *_ = name.split("/")
    return vertexai.Client(project=project, location=location).agent_engines.get(name=name)


class Liberacao(BaseModel):
    """Simulated answer of the bank's credit service (demo parameter); mirrors mcp_server.core.tipos."""

    cobertura: bool = True
    consignado: bool = True
    prestamista: bool = True


class NovaSessao(BaseModel):
    """A session is one trigger in one mode. Granting or revoking consent, or a new trigger, starts a new
    session: state is set here, server-side, and never written by the front afterwards."""

    cliente_id: str
    modo: Literal["insight", "conversa"] = "conversa"
    gatilho: Literal["fechamento", "pagar_outro_valor", "pergunta_cliente", "acompanhamento"] = "pergunta_cliente"
    consentimento: bool = False
    liberacao: Liberacao = Liberacao()


class RunRequest(BaseModel):
    sessao_id: str
    new_message: dict[str, Any]  # google.genai Content: {"role": "user", "parts": [{"text": ...}]}


@app.exception_handler(errors.APIError)
async def agent_engine_error(request, exc: errors.APIError):
    return JSONResponse(status_code=exc.code or 502, content={"detail": exc.message})


def require_uid(uid: str | None) -> str:
    if not _UID.fullmatch(uid or ""):
        raise HTTPException(401, "Sessão não iniciada: chame POST /sessao.")
    return uid


@app.post("/sessao")
async def criar_sessao(body: NovaSessao, response: Response, uid: Uid = None):
    if body.cliente_id not in CLIENTES_DEMO:
        raise HTTPException(404, "Cliente não encontrado.")
    if not _UID.fullmatch(uid or ""):
        uid = uuid.uuid4().hex
    estado = {
        "consentimento": body.consentimento,
        "primeiro_nome": CLIENTES_DEMO[body.cliente_id],
        "liberacao": body.liberacao.model_dump(),
    }
    state = {"cliente_id": body.cliente_id, "modo": body.modo, "gatilho": body.gatilho, "estado": estado}
    session = await engine().async_create_session(user_id=uid, state=state, ttl=f"{SESSION_TTL_S}s")
    response.set_cookie("uid", uid, max_age=SESSION_TTL_S, httponly=True, secure=True, samesite="lax")
    return {"sessao_id": session["id"]}


@app.get("/sessao/{sessao_id}")
async def ver_sessao(sessao_id: str, uid: Uid = None):
    return await engine().async_get_session(user_id=require_uid(uid), session_id=sessao_id)


def events(req: RunRequest, uid: str | None):
    return engine().async_stream_query(user_id=require_uid(uid), session_id=req.sessao_id, message=req.new_message)


@app.post("/run")
async def run(req: RunRequest, uid: Uid = None):
    return [event async for event in events(req, uid)]


@app.post("/run_sse")
async def run_sse(req: RunRequest, uid: Uid = None):
    stream = events(req, uid)  # validate before the 200 goes out

    async def sse():
        async for event in stream:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
