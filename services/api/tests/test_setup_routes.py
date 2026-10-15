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
