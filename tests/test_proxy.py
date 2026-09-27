from fastapi.testclient import TestClient

from proxy import app as proxy

CLIENTE = "3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b"
UID = "0" * 32
RUN = {"sessao_id": "s1", "new_message": {"role": "user", "parts": [{"text": "oi"}]}}


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def async_create_session(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "s1", "userId": kwargs["user_id"]}

    async def async_stream_query(self, **kwargs):
        self.calls.append(kwargs)
        for i in range(2):
            yield {"id": f"e{i}"}


def client(monkeypatch, uid=None):
    fake = FakeEngine()
    monkeypatch.setattr(proxy, "engine", lambda: fake)
    http = TestClient(proxy.app)
    if uid:
        http.cookies.set("uid", uid)
    return http, fake


def test_sessao_sets_state_and_cookie_ignoring_client_state(monkeypatch):
    http, fake = client(monkeypatch)
    resp = http.post("/sessao", json={"cliente_id": CLIENTE, "state": {"cliente_id": "outro"}})
    assert resp.json() == {"sessao_id": "s1"}
    [call] = fake.calls
    assert call["state"] == {"cliente_id": CLIENTE}
    assert call["ttl"] == "86400s"
    cookie = resp.headers["set-cookie"]
    assert f"uid={call['user_id']}" in cookie and "HttpOnly" in cookie and "Secure" in cookie


def test_sessao_reuses_valid_uid_and_replaces_invalid(monkeypatch):
    http, fake = client(monkeypatch, uid=UID)
    http.post("/sessao", json={"cliente_id": CLIENTE})
    http.cookies.set("uid", "../not-a-uid")
    http.post("/sessao", json={"cliente_id": CLIENTE})
    assert fake.calls[0]["user_id"] == UID
    assert fake.calls[1]["user_id"] not in (UID, "../not-a-uid")


def test_sessao_rejects_unknown_cliente(monkeypatch):
    http, fake = client(monkeypatch)
    assert http.post("/sessao", json={"cliente_id": "qualquer"}).status_code == 404
    assert fake.calls == []


def test_run_requires_cookie(monkeypatch):
    http, fake = client(monkeypatch)
    assert http.post("/run", json=RUN).status_code == 401
    assert http.post("/run_sse", json=RUN).status_code == 401
    assert fake.calls == []


def test_run_collects_events_for_cookie_user(monkeypatch):
    http, fake = client(monkeypatch, uid=UID)
    assert http.post("/run", json=RUN).json() == [{"id": "e0"}, {"id": "e1"}]
    assert fake.calls == [{"user_id": UID, "session_id": "s1", "message": RUN["new_message"]}]


def test_run_sse_streams_events(monkeypatch):
    http, _ = client(monkeypatch, uid=UID)
    assert http.post("/run_sse", json=RUN).text == 'data: {"id": "e0"}\n\ndata: {"id": "e1"}\n\n'
