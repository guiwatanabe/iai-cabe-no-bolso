from fastapi.testclient import TestClient

from proxy import app as proxy

RUN = {
    "app_name": "cabe",
    "user_id": "u",
    "session_id": "s1",
    "new_message": {"role": "user", "parts": [{"text": "oi"}]},
}


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def async_create_session(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": kwargs["session_id"] or "generated", "userId": kwargs["user_id"]}

    async def async_stream_query(self, **kwargs):
        self.calls.append(kwargs)
        for i in range(2):
            yield {"id": f"e{i}"}


def client(monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr(proxy, "engine", lambda: fake)
    return TestClient(proxy.app), fake


def test_create_session_with_id_forwards_state(monkeypatch):
    http, fake = client(monkeypatch)
    resp = http.post("/apps/cabe/users/u/sessions/s1", json={"k": 1})
    assert resp.json() == {"id": "s1", "userId": "u"}
    assert fake.calls == [{"user_id": "u", "session_id": "s1", "state": {"k": 1}}]


def test_run_collects_events(monkeypatch):
    http, fake = client(monkeypatch)
    assert http.post("/run", json=RUN).json() == [{"id": "e0"}, {"id": "e1"}]
    assert fake.calls == [{"user_id": "u", "session_id": "s1", "message": RUN["new_message"]}]


def test_run_sse_streams_events(monkeypatch):
    http, _ = client(monkeypatch)
    assert http.post("/run_sse", json=RUN).text == 'data: {"id": "e0"}\n\ndata: {"id": "e1"}\n\n'


def test_unknown_app_is_404(monkeypatch):
    http, fake = client(monkeypatch)
    assert http.post("/run", json={**RUN, "app_name": "other"}).status_code == 404
    assert http.post("/apps/other/users/u/sessions").status_code == 404
    assert fake.calls == []
