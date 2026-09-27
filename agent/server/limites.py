"""Limites e cabeçalhos de segurança (middleware ASGI puro, sem dependência além do Starlette).

- rate limit por IP em memória (janela deslizante) só em /api;
- tamanho máximo do corpo (exige Content-Length em POST para /api);
- concorrência máxima em /api (503 quando cheio);
- origem: requisição com Origin de outro site é recusada (CORS só mesma origem; nenhum cabeçalho CORS é emitido);
- cabeçalhos básicos de segurança em todas as respostas; Cache-Control: no-store em /api.

Erros saem no formato da API: {erro, mensagem_cliente}. Nada aqui registra IP ou corpo em log.
"""
from __future__ import annotations

import json
import time
from collections import deque
from urllib.parse import urlsplit

CSP = (
    "default-src 'self'; script-src 'self'; script-src-attr 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
CABECALHOS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"content-security-policy", CSP.encode()),
]


def _json(status: int, erro: str, mensagem: str, extras: list[tuple[bytes, bytes]] | None = None) -> tuple[dict, dict]:
    corpo = json.dumps({"erro": erro, "mensagem_cliente": mensagem}, ensure_ascii=False).encode()
    headers = [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(corpo)).encode()),
               (b"cache-control", b"no-store")] + list(extras or []) + CABECALHOS
    return ({"type": "http.response.start", "status": status, "headers": headers},
            {"type": "http.response.body", "body": corpo})


class Limites:
    def __init__(self, app, *, prefixo_api: str = "/api", max_corpo: int = 16 * 1024, max_concorrencia: int = 40,
                 req_por_minuto: int = 60, janela_s: int = 60):
        self.app = app
        self.prefixo = prefixo_api
        self.max_corpo = int(max_corpo)
        self.max_concorrencia = int(max_concorrencia)
        self.req_por_janela = int(req_por_minuto)
        self.janela_s = int(janela_s)
        self.em_curso = 0
        self._por_ip: dict[str, deque] = {}

    # ---------------------------------------------------------------- utilidades
    @staticmethod
    def _header(scope: dict, nome: bytes) -> str | None:
        for k, v in scope.get("headers") or []:
            if k == nome:
                return v.decode("latin-1")
        return None

    def _ip(self, scope: dict) -> str:
        xff = self._header(scope, b"x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        cliente = scope.get("client")
        return cliente[0] if cliente else "desconhecido"

    def _estourou_rate(self, ip: str, agora: float) -> bool:
        fila = self._por_ip.get(ip)
        if fila is None:
            if len(self._por_ip) > 10_000:  # limpeza grosseira: descarta filas vazias/antigas
                self._por_ip = {k: q for k, q in self._por_ip.items() if q and agora - q[-1] < self.janela_s}
            fila = self._por_ip[ip] = deque()
        while fila and agora - fila[0] >= self.janela_s:
            fila.popleft()
        if len(fila) >= self.req_por_janela:
            return True
        fila.append(agora)
        return False

    def _origem_ok(self, scope: dict) -> bool:
        origem = self._header(scope, b"origin")
        if not origem or origem == "null":
            return origem != "null"
        host = self._header(scope, b"host") or ""
        return urlsplit(origem).netloc.lower() == host.lower()

    # ---------------------------------------------------------------- ASGI
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        caminho = scope.get("path", "")
        e_api = caminho.startswith(self.prefixo)
        metodo = scope.get("method", "GET").upper()
        https = scope.get("scheme") == "https" or (self._header(scope, b"x-forwarded-proto") or "").lower() == "https"

        async def send_com_cabecalhos(mensagem):
            if mensagem["type"] == "http.response.start":
                headers = list(mensagem.get("headers") or [])
                presentes = {k for k, _ in headers}
                for k, v in CABECALHOS:
                    if k not in presentes:
                        headers.append((k, v))
                if e_api and b"cache-control" not in presentes:
                    headers.append((b"cache-control", b"no-store"))
                if https and b"strict-transport-security" not in presentes:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                mensagem = {**mensagem, "headers": headers}
            await send(mensagem)

        if not e_api:
            return await self.app(scope, receive, send_com_cabecalhos)

        if metodo in ("POST", "PUT", "PATCH", "DELETE"):
            if not self._origem_ok(scope) or (self._header(scope, b"sec-fetch-site") or "").lower() == "cross-site":
                inicio, corpo = _json(403, "origem_nao_permitida", "Esta página só aceita pedidos da própria demo.")
                await send(inicio); await send(corpo); return
            tamanho = self._header(scope, b"content-length")
            if tamanho is None:
                if (self._header(scope, b"transfer-encoding") or "").lower() == "chunked":
                    inicio, corpo = _json(411, "tamanho_obrigatorio", "Não entendi o pedido. Tente de novo.")
                    await send(inicio); await send(corpo); return
                tamanho = "0"
            if not tamanho.isdigit() or int(tamanho) > self.max_corpo:
                inicio, corpo = _json(413, "corpo_grande", "A mensagem é grande demais. Escreva em poucas linhas.")
                await send(inicio); await send(corpo); return

        if self._estourou_rate(self._ip(scope), time.monotonic()):
            inicio, corpo = _json(429, "muitas_requisicoes", "Muitas tentativas em pouco tempo. Espere um minuto e tente de novo.",
                                  [(b"retry-after", str(self.janela_s).encode())])
            await send(inicio); await send(corpo); return

        if self.em_curso >= self.max_concorrencia:
            inicio, corpo = _json(503, "servico_ocupado", "Estou com muita gente agora. Tente de novo em instantes.",
                                  [(b"retry-after", b"2")])
            await send(inicio); await send(corpo); return

        self.em_curso += 1
        try:
            await self.app(scope, receive, send_com_cabecalhos)
        finally:
            self.em_curso -= 1
