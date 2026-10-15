import httpx

from app.api.routes import setup as setup_routes
from tests.conftest import ndjson


def _tags(*names):
    def handler(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": n} for n in names]})

    return handler


def test_routes_404_in_server_mode(make_client):
    client = make_client(_tags(), mode="server")
    assert client.get("/setup/status").status_code == 404
    assert client.post("/setup/pull", json={"model": "llama3.2"}).status_code == 404


def test_requires_owner(make_client):
    client = make_client(_tags(), role="member")
    assert client.get("/setup/status").status_code == 403


def test_status_ollama_not_running(make_client):
    def handler(request):
        raise httpx.ConnectError("refused")

    body = make_client(handler).get("/setup/status").json()
    assert body["ollama"] == {"running": False, "models": []}
    assert body["setup_complete"] is False
    assert body["embedding_provider"] == "ollama"
    assert body["required_models"] == ["nomic-embed-text", "llama3.2"]


def test_status_partial_models(make_client):
    body = make_client(_tags("nomic-embed-text:latest", "qwen3:8b")).get("/setup/status").json()
    assert body["ollama"]["running"] is True
    assert "nomic-embed-text" in body["ollama"]["models"]
    assert body["answer_model_candidates"] == ["qwen3:8b"]
    assert body["setup_complete"] is False


def test_pull_relays_progress(make_client):
    def handler(request):
        assert request.url.path == "/api/pull"
        return httpx.Response(
            200,
            text=ndjson(
                {"status": "pulling", "completed": 10, "total": 100},
                {"status": "pulling", "completed": 50, "total": 100},
                {"status": "pulling", "completed": 100, "total": 100},
                {"status": "success"},
            ),
        )

    client = make_client(handler)
    text = client.post("/setup/pull", json={"model": "llama3.2"}).text
    assert text.count("event: progress") >= 2
    assert text.rstrip().endswith("data: {}")
    assert text.rfind("event: done") > text.rfind("event: progress")
    assert setup_routes._IN_FLIGHT == set()


def test_pull_error_surfaces_message(make_client):
    def handler(request):
        return httpx.Response(200, text=ndjson({"error": "no space left on device"}))

    text = make_client(handler).post("/setup/pull", json={"model": "llama3.2"}).text
    assert "event: error" in text
    assert "no space left on device" in text
    assert "event: done" not in text
    assert setup_routes._IN_FLIGHT == set()


def test_pull_conflict(make_client):
    client = make_client(_tags())
    setup_routes._IN_FLIGHT.add("llama3.2")
    r = client.post("/setup/pull", json={"model": "llama3.2"})
    assert r.status_code == 409
    assert r.json()["detail"] == "Already downloading llama3.2."


def test_pull_rejects_unknown_model(make_client):
    r = make_client(_tags("qwen3:8b")).post("/setup/pull", json={"model": "evil:1b"})
    assert r.status_code == 400


def test_pull_accepts_installed_answer_candidate(make_client):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        return httpx.Response(200, text=ndjson({"status": "success"}))

    r = make_client(handler).post("/setup/pull", json={"model": "qwen3:8b"})
    assert r.status_code == 200 and "event: done" in r.text


# --- completing setup ---------------------------------------------------------

import json as _json

from tests.conftest import db_run

ANTH = "api.anthropic.com"


def _sse_anthropic(text="OK"):
    ev = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    return f"event: content_block_delta\ndata: {_json.dumps(ev)}\n\n"


def _keys_handler(voyage=200, models=("claude-sonnet-5-5",)):
    def handler(request):
        host = request.url.host
        if host == "api.voyageai.com":
            if voyage != 200:
                return httpx.Response(voyage, json={"detail": "Provided API key is invalid."})
            return httpx.Response(200, json={"data": [{"embedding": [0.1]}], "usage": {"total_tokens": 1}})
        if host == ANTH and request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in models]})
        if host == ANTH:
            return httpx.Response(200, text=_sse_anthropic(), headers={"content-type": "text/event-stream"})
        raise AssertionError(f"unexpected call {request.url}")

    return handler


def _desktop_json(tmp_path):
    p = tmp_path / "desktop.json"
    return _json.loads(p.read_text()) if p.exists() else None


def _secrets(tmp_path):
    p = tmp_path / "secrets.json"
    return _json.loads(p.read_text()) if p.exists() else {}


def test_complete_local_saves_and_restarts(make_client, pg_dsn, restarts, tmp_path):
    client = make_client(_tags("nomic-embed-text:latest", "llama3.2:latest"), db=True)
    r = client.post("/setup/complete-local", json={"answer_model": "llama3.2:latest"})
    assert r.status_code == 200 and r.json() == {"restarting": True}
    assert _desktop_json(tmp_path)["setup_complete"] is True
    assert _desktop_json(tmp_path)["embedding_provider"] == "ollama"
    rows = db_run(pg_dsn, "SELECT provider FROM org_llm_settings WHERE org_id = $1", client.org_id)
    assert [r["provider"] for r in rows] == ["local"]
    assert len(restarts) == 1


def test_complete_local_missing_model(make_client, pg_dsn, restarts, tmp_path):
    client = make_client(_tags("llama3.2"), db=True)
    r = client.post("/setup/complete-local", json={"answer_model": "llama3.2"})
    assert r.status_code == 400 and "nomic-embed-text" in r.json()["detail"]
    assert restarts == [] and _desktop_json(tmp_path) is None


def test_complete_local_missing_answer_model(make_client, pg_dsn, restarts):
    client = make_client(_tags("nomic-embed-text"), db=True)
    r = client.post("/setup/complete-local", json={"answer_model": "qwen3:8b"})
    assert r.status_code == 400 and "qwen3:8b" in r.json()["detail"]
    assert restarts == []


def test_complete_keys_requires_owner_and_desktop(make_client):
    body = {"voyage_api_key": "v", "provider": "anthropic", "api_key": "k"}
    assert make_client(_tags(), mode="server").post("/setup/complete-keys", json=body).status_code == 404
    assert make_client(_tags(), role="member").post("/setup/complete-keys", json=body).status_code == 403


def test_complete_keys_bad_voyage_key_writes_nothing(make_client, external, pg_dsn, restarts, tmp_path):
    client = make_client(_tags(), db=True)
    external.handler = _keys_handler(voyage=401)
    r = client.post(
        "/setup/complete-keys", json={"voyage_api_key": "bad", "provider": "anthropic", "api_key": "sk-ant-1234567890"}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["field"] == "voyage_api_key"
    assert "invalid" in r.json()["detail"]["message"].lower()
    assert _desktop_json(tmp_path) is None
    assert "voyage_api_key" not in _secrets(tmp_path)
    assert db_run(pg_dsn, "SELECT 1 FROM org_llm_settings WHERE org_id = $1", client.org_id) == []
    assert db_run(pg_dsn, "SELECT 1 FROM org_llm_credentials WHERE org_id = $1", client.org_id) == []
    assert restarts == []


def test_complete_keys_unknown_model(make_client, external, pg_dsn, restarts, tmp_path):
    client = make_client(_tags(), db=True)
    external.handler = _keys_handler(models=("claude-haiku-9", "claude-opus-9"))
    r = client.post(
        "/setup/complete-keys",
        json={"voyage_api_key": "v", "provider": "anthropic", "api_key": "sk-ant-1234567890", "model": "nope"},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["field"] == "model"
    assert "claude-haiku-9" in detail["message"] and "claude-opus-9" in detail["message"]
    assert _desktop_json(tmp_path) is None and "voyage_api_key" not in _secrets(tmp_path)
    assert db_run(pg_dsn, "SELECT 1 FROM org_llm_credentials WHERE org_id = $1", client.org_id) == []
    assert restarts == []


def test_complete_keys_bad_answer_key(make_client, external, pg_dsn, restarts, tmp_path):
    client = make_client(_tags(), db=True)

    def handler(request):
        if request.url.host == ANTH:
            return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})
        return _keys_handler()(request)

    external.handler = handler
    r = client.post(
        "/setup/complete-keys", json={"voyage_api_key": "v", "provider": "anthropic", "api_key": "sk-ant-1234567890"}
    )
    assert r.status_code == 400 and r.json()["detail"]["field"] == "api_key"
    assert _desktop_json(tmp_path) is None and restarts == []


def test_complete_keys_anthropic_success(make_client, external, pg_dsn, restarts, tmp_path):
    client = make_client(_tags(), db=True)
    external.handler = _keys_handler()
    r = client.post(
        "/setup/complete-keys", json={"voyage_api_key": "pa-voy", "provider": "anthropic", "api_key": "sk-ant-1234567890"}
    )
    assert r.status_code == 200 and r.json() == {"restarting": True}
    assert _desktop_json(tmp_path) == {"setup_complete": True, "embedding_provider": "voyage"}
    assert _secrets(tmp_path)["voyage_api_key"] == "pa-voy"
    rows = db_run(pg_dsn, "SELECT provider FROM org_llm_settings WHERE org_id = $1", client.org_id)
    assert [r["provider"] for r in rows] == ["anthropic"]
    creds = db_run(pg_dsn, "SELECT model FROM org_llm_credentials WHERE org_id = $1", client.org_id)
    assert [c["model"] for c in creds] == ["claude-sonnet-5-5"]
    assert len(restarts) == 1


def test_complete_keys_gemini_success(make_client, external, pg_dsn, restarts, tmp_path, monkeypatch):
    from google import genai

    class FakeModels:
        async def list(self):
            async def gen():
                for n in ("models/gemini-3.6-flash", "models/other"):
                    yield type("M", (), {"name": n})()

            return gen()

    monkeypatch.setattr(genai, "Client", lambda api_key: type("C", (), {"aio": type("A", (), {"models": FakeModels()})()})())
    client = make_client(_tags(), db=True)
    external.handler = _keys_handler()
    r = client.post("/setup/complete-keys", json={"voyage_api_key": "v", "provider": "gemini", "api_key": "AIza123"})
    assert r.status_code == 200
    s = _secrets(tmp_path)
    assert s["google_api_key"] == "AIza123" and s["voyage_api_key"] == "v"
    rows = db_run(pg_dsn, "SELECT provider FROM org_llm_settings WHERE org_id = $1", client.org_id)
    assert [r["provider"] for r in rows] == ["gemini"]
    assert len(restarts) == 1


def test_put_llm_unchanged_after_refactor(make_client, pg_dsn, tmp_path):
    from fastapi import FastAPI
    from app.api.routes import llm_settings

    client = make_client(_tags(), db=True)
    client.app.include_router(llm_settings.router, prefix="/settings")
    r = client.put("/settings/llm", json={"provider": "local", "model": "llama3.2", "base_url": "http://127.0.0.1:11434/v1"})
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "local" and body["model"] == "llama3.2"
    assert body["base_url"] == "http://127.0.0.1:11434/v1" and body["has_api_key"] is False
    assert set(body) == {"provider", "default_model", "providers", "model", "base_url", "has_api_key", "api_key_hint"}


def test_status_reports_installed_embed_model_under_any_tag(make_client):
    body = make_client(_tags("nomic-embed-text:v1.5", "qwen3.6:35b")).get("/setup/status").json()
    assert body["embed_model"] == "nomic-embed-text:v1.5"
    assert body["answer_model_candidates"] == ["qwen3.6:35b"]


def test_status_embed_model_none_when_missing(make_client):
    body = make_client(_tags("qwen3.6:35b")).get("/setup/status").json()
    assert body["embed_model"] is None


def test_complete_local_accepts_tagged_embed_model_and_saves_it(make_client, pg_dsn, restarts, tmp_path):
    client = make_client(_tags("nomic-embed-text:v1.5", "qwen3.6:35b"), db=True)
    r = client.post("/setup/complete-local", json={"answer_model": "qwen3.6:35b"})
    assert r.status_code == 200
    assert _desktop_json(tmp_path)["ollama_embed_model"] == "nomic-embed-text:v1.5"
    assert len(restarts) == 1
