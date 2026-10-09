"""Client isolation via X-Client-Id (CHANGES-v2 §4)."""

import pytest

from tests.test_api import CLIENT_B, chat, client, script_generate  # noqa: F401


def test_health_needs_no_client_id(client):
    assert client.get("/api/health", headers={"X-Client-Id": ""}).status_code == 200


@pytest.mark.parametrize("value", ["", "not-a-uuid", "1234"])
@pytest.mark.parametrize(
    "method, path",
    [("get", "/api/tables"), ("get", "/api/threads"), ("get", "/api/saved"), ("post", "/api/chat")],
)
def test_missing_or_malformed_client_id_is_400(client, value, method, path):
    kwargs = {"json": {"thread_id": "t", "message": "hi"}} if method == "post" else {}
    r = getattr(client, method)(path, headers={"X-Client-Id": value}, **kwargs)
    assert r.status_code == 400
    assert r.json() == {"detail": "Missing or invalid X-Client-Id header"}


def test_400_still_carries_cors_headers(client):
    r = client.get(
        "/api/threads",
        headers={"X-Client-Id": "", "Origin": "https://sql-agent-frontend-delta.vercel.app"},
    )
    assert r.status_code == 400
    assert r.headers["access-control-allow-origin"] == "https://sql-agent-frontend-delta.vercel.app"


def test_client_a_cannot_see_client_b_threads(client):
    script_generate(client.fake)
    chat(client, "List product names", thread_id="a-thread")
    client.post(
        "/api/saved",
        json={"title": "Mine", "prompt": "p", "sql": "SELECT Name FROM Products", "dialect": "sqlite"},
    )
    b = {"X-Client-Id": CLIENT_B}
    assert client.get("/api/threads", headers=b).json() == []
    assert client.get("/api/saved", headers=b).json() == []
    for method, path in [
        ("get", "/api/threads/a-thread"),
        ("delete", "/api/threads/a-thread"),
        ("post", "/api/threads/a-thread/duplicate"),
    ]:
        assert getattr(client, method)(path, headers=b).status_code == 404
    assert client.patch("/api/threads/a-thread", json={"title": "x"}, headers=b).status_code == 404
    r = client.post("/api/chat", json={"thread_id": "a-thread", "message": "hi"}, headers=b)
    assert r.status_code == 404
    # Client A still has everything.
    assert client.get("/api/threads/a-thread").status_code == 200
    assert len(client.get("/api/saved").json()) == 1


def test_client_id_is_case_insensitive(client):
    script_generate(client.fake)
    chat(client, "List product names", thread_id="case")
    upper = {"X-Client-Id": client.headers["X-Client-Id"].upper()}
    assert client.get("/api/threads/case", headers=upper).status_code == 200
