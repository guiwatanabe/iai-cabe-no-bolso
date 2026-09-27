"""Thin proxy: the ADK api_server routes, forwarded to the `cabe` agent on Vertex AI Agent Engine.

The agent, its config and its sessions live in Agent Engine; this service only relays.
"""

import json
import os
from functools import cache
from typing import Any

import vertexai
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from google.genai import errors
from pydantic import BaseModel

APP_NAME = "cabe"

app = FastAPI(title="cabe-no-bolso")


@cache
def engine():
    name = os.environ["AGENT_ENGINE"]  # projects/{project}/locations/{location}/reasoningEngines/{id}
    _, project, _, location, *_ = name.split("/")
    return vertexai.Client(project=project, location=location).agent_engines.get(name=name)


class CreateSession(BaseModel):
    session_id: str | None = None
    state: dict[str, Any] | None = None


class RunRequest(BaseModel):
    app_name: str
    user_id: str
    session_id: str
    new_message: dict[str, Any]  # google.genai Content: {"role": "user", "parts": [{"text": ...}]}


@app.exception_handler(errors.APIError)
async def agent_engine_error(request, exc: errors.APIError):
    return JSONResponse(status_code=exc.code or 502, content={"detail": exc.message})


def check_app(app_name: str):
    if app_name != APP_NAME:
        raise HTTPException(404, f"App not found: {app_name}")


@app.post("/apps/{app_name}/users/{user_id}/sessions")
async def create_session(app_name: str, user_id: str, body: CreateSession | None = None):
    check_app(app_name)
    body = body or CreateSession()
    return await engine().async_create_session(user_id=user_id, session_id=body.session_id, state=body.state)


@app.post("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
async def create_session_with_id(app_name: str, user_id: str, session_id: str, state: dict[str, Any] | None = None):
    check_app(app_name)
    return await engine().async_create_session(user_id=user_id, session_id=session_id, state=state)


@app.get("/apps/{app_name}/users/{user_id}/sessions")
async def list_sessions(app_name: str, user_id: str):
    check_app(app_name)
    return await engine().async_list_sessions(user_id=user_id)


@app.get("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
async def get_session(app_name: str, user_id: str, session_id: str):
    check_app(app_name)
    return await engine().async_get_session(user_id=user_id, session_id=session_id)


@app.delete("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
async def delete_session(app_name: str, user_id: str, session_id: str):
    check_app(app_name)
    await engine().async_delete_session(user_id=user_id, session_id=session_id)


def events(req: RunRequest):
    check_app(req.app_name)
    return engine().async_stream_query(user_id=req.user_id, session_id=req.session_id, message=req.new_message)


@app.post("/run")
async def run(req: RunRequest):
    return [event async for event in events(req)]


@app.post("/run_sse")
async def run_sse(req: RunRequest):
    stream = events(req)  # validate before the 200 goes out

    async def sse():
        async for event in stream:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
