"""Tests for the web layer: routes, the WSGI app, the local server and the Vercel setup."""

import http.client
import importlib.util
import io
import json
import threading
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from dpi_agent import config
from dpi_agent.llm.providers import DeterministicProvider
from dpi_agent.web import api
from dpi_agent.web.api import SECURITY_HEADERS, RateLimiter, Request, dispatch, parse_int
from dpi_agent.web.server import create_server
from dpi_agent.web.wsgi import app as wsgi_app

ROOT = Path(__file__).resolve().parents[1]
HOST = {"host": "127.0.0.1:8000"}
SUBSIDY = "I am Suresh Kaur, ID 765-741-434. I want to apply for a solar pump subsidy."


def get(path, headers=None):
    query = {}
    if "?" in path:
        path, raw = path.split("?", 1)
        query = parse_qs(raw)
    return dispatch(Request("GET", path, query, {**HOST, **(headers or {})}))


def post(path, body, content_type="application/json", headers=None):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return dispatch(
        Request("POST", path, {}, {**HOST, "content-type": content_type, **(headers or {})}, data)
    )


def payload(response):
    return json.loads(response.body)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_parse_int():
    assert parse_int(None, 25, 1, 200) == 25
    assert parse_int("abc", 25, 1, 200) == 25
    assert parse_int("-1", 25, 1, 200) == 1
    assert parse_int("100000", 25, 1, 200) == 200


def test_health_lists_every_provider():
    data = payload(get("/api/health"))
    assert set(data["providers"]) == {"deterministic", "ollama", "groq"}
    assert data["providers"]["deterministic"]["available"] is True
    groq = data["providers"]["groq"]
    assert groq["available"] is False and "GROQ_API_KEY" in groq["error"]
    assert data["database"]["tables"]["identity"] == 10_000
    assert data["deployment"] == "local"


def test_chat_with_rules():
    response = post("/api/chat", {"query": SUBSIDY, "provider": "deterministic"})
    assert response.status == 200
    result = payload(response)
    assert result["status"] == "COMPLETED"
    assert result["interpreter"] == "deterministic"
    assert all("stage" in step for step in result["steps"])


def test_chat_legacy_flag_without_live_model_uses_rules():
    result = payload(post("/api/chat", {"query": SUBSIDY, "use_live_slm": False}))
    assert result["interpreter"] == "deterministic"


def test_chat_rejects_unknown_provider():
    assert post("/api/chat", {"query": SUBSIDY, "provider": "gpt"}).status == 400


@pytest.mark.parametrize(
    "body, status",
    [
        ({"query": ""}, 400),
        ({"query": "x" * 5000}, 400),
        (b"[1, 2]", 400),
        (b"{not json", 400),
    ],
)
def test_chat_input_validation(body, status):
    assert post("/api/chat", body).status == status


def test_cross_site_simple_post_rejected():
    """text/plain skips the CORS pre-flight, so it must be refused."""
    response = post("/api/chat", {"query": SUBSIDY}, content_type="text/plain")
    assert response.status == 415


def test_oversized_body_rejected():
    request = Request(
        "POST",
        "/api/chat",
        {},
        {**HOST, "content-type": "application/json"},
        b"",
        body_too_large=True,
    )
    assert dispatch(request).status == 413


def test_foreign_host_rejected_locally():
    """DNS-rebinding style requests carry a non-loopback Host header."""
    assert get("/api/health", {"host": "attacker.example"}).status == 403


def test_any_host_allowed_when_configured(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_HOSTS", ["*"])
    assert get("/api/health", {"host": "my-app.vercel.app"}).status == 200


def test_scenario_route():
    assert payload(post("/api/scenario/b", {}))["status"] == "DENIED_CONSENT"
    assert post("/api/scenario/z", {}).status == 404


def test_audit_routes():
    post("/api/scenario/a", {})
    records = payload(get("/api/audit/workflow?limit=5"))["records"]
    assert records and records[0]["hash"]
    verify = payload(get("/api/audit/verify"))
    assert verify["ok"] is True
    assert set(verify["logs"]) == {"consent", "workflow", "escalations"}
    assert get("/api/audit/nope").status == 404


def test_registry_paging_and_masking():
    data = payload(get("/api/registries/identity?limit=abc&offset=xyz"))
    assert len(data["rows"]) == 25
    assert payload(get("/api/registries/identity?limit=100000"))["limit"] == 200
    for row in data["rows"]:
        assert row["phone_number"] == "****"
    assert payload(get("/api/registries/agriculture?q=Coimbatore&limit=3"))["total"] > 0


def test_unknown_routes():
    assert get("/api/nope").status == 404
    assert dispatch(Request("DELETE", "/api/chat", {}, HOST)).status == 405


def test_security_headers_on_every_response():
    for response in (get("/api/health"), get("/"), get("/missing")):
        headers = dict(response.header_list())
        for name, value in SECURITY_HEADERS.items():
            assert headers[name] == value
        assert "Access-Control-Allow-Origin" not in headers


def test_rate_limit_applies_to_model_requests(monkeypatch):
    class FakeModel(DeterministicProvider):
        @property
        def name(self):
            return "fake:model"

    registry = api.ProviderRegistry()
    registry.use("ollama", FakeModel())
    monkeypatch.setattr(api, "providers", registry)
    monkeypatch.setattr(api, "model_rate_limiter", RateLimiter(1))

    assert post("/api/chat", {"query": SUBSIDY, "provider": "ollama"}).status == 200
    limited = post("/api/chat", {"query": SUBSIDY, "provider": "ollama"})
    assert limited.status == 429
    assert int(dict(limited.header_list())["Retry-After"]) >= 1
    # rules-only requests are never limited
    assert post("/api/chat", {"query": SUBSIDY, "provider": "deterministic"}).status == 200


def test_rate_limiter_window():
    limiter = RateLimiter(2)
    assert limiter.check("a") == 0 and limiter.check("a") == 0
    assert limiter.check("a") > 0
    assert limiter.check("b") == 0
    assert RateLimiter(0).check("a") == 0


def test_client_ip_from_vercel_proxy_headers(monkeypatch):
    request = Request(
        "GET", "/", headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"}, client_ip="127.0.0.1"
    )
    assert api._client_key(request) == "127.0.0.1"
    monkeypatch.setattr(config, "IS_VERCEL", True)
    assert api._client_key(request) == "203.0.113.9"


# ---------------------------------------------------------------------------
# Static website
# ---------------------------------------------------------------------------


def test_static_index_and_assets():
    index = get("/")
    assert index.status == 200 and index.content_type.startswith("text/html")
    assert b"DPI Public Agent" in index.body
    script = get("/assets/app.js")
    assert script.content_type.startswith("text/javascript")
    assert get("/assets/styles.css").content_type.startswith("text/css")


@pytest.mark.parametrize(
    "path", ["/../pyproject.toml", "/%2e%2e/pyproject.toml", "/..%2f.env", "/missing.html"]
)
def test_static_cannot_escape_public_folder(path):
    assert get(path).status == 404


def test_page_has_no_inline_scripts_or_styles():
    """The Content-Security-Policy forbids inline code; the page must not need it."""
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html and " style=" not in html and "onclick=" not in html


# ---------------------------------------------------------------------------
# WSGI app (what Vercel runs)
# ---------------------------------------------------------------------------


def call_wsgi(method, path, body=b"", headers=None, query=""):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_HOST": "127.0.0.1:8000",
    }
    for name, value in (headers or {}).items():
        key = name.upper().replace("-", "_")
        environ[key if key == "CONTENT_TYPE" else f"HTTP_{key}"] = value
    captured = {}

    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    chunks = wsgi_app(environ, start_response)
    return captured["status"], captured["headers"], b"".join(chunks)


def test_wsgi_chat():
    status, headers, body = call_wsgi(
        "POST",
        "/api/chat",
        json.dumps({"query": SUBSIDY, "provider": "deterministic"}).encode(),
        {"Content-Type": "application/json"},
    )
    assert status == "200 OK"
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body)["status"] == "COMPLETED"


def test_wsgi_registry_query_string():
    status, _, body = call_wsgi("GET", "/api/registries/credit", query="limit=3")
    assert status == "200 OK" and len(json.loads(body)["rows"]) == 3


def test_wsgi_head_has_no_body():
    status, headers, body = call_wsgi("HEAD", "/")
    assert status == "200 OK" and body == b"" and int(headers["Content-Length"]) > 0


# ---------------------------------------------------------------------------
# Local development server (real sockets)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def local_server():
    server = create_server("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def local_request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response, response.read()
    finally:
        conn.close()


def test_local_server_end_to_end(local_server):
    response, body = local_request(
        local_server,
        "POST",
        "/api/chat",
        json.dumps({"query": SUBSIDY, "provider": "deterministic"}),
        {"Content-Type": "application/json"},
    )
    assert response.status == 200 and json.loads(body)["status"] == "COMPLETED"
    response, body = local_request(local_server, "GET", "/")
    assert response.status == 200 and b"<main" in body
    response, _ = local_request(
        local_server, "GET", "/api/health", headers={"Host": "evil.example"}
    )
    assert response.status == 403


def test_local_server_refuses_huge_body(local_server):
    response, _ = local_request(
        local_server,
        "POST",
        "/api/chat",
        b"x" * (api.MAX_BODY_BYTES + 10),
        {"Content-Type": "application/json"},
    )
    assert response.status == 413


# ---------------------------------------------------------------------------
# Vercel configuration
# ---------------------------------------------------------------------------


def test_vercel_entry_exposes_wsgi_app():
    spec = importlib.util.spec_from_file_location("vercel_entry", ROOT / "api" / "index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.app is wsgi_app


def test_vercel_json_matches_the_app():
    settings = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert settings["framework"] is None
    assert (ROOT / settings["outputDirectory"] / "index.html").is_file()
    assert {"source": "/api/:path*", "destination": "/api/index"} in settings["rewrites"]
    assert "api/index.py" in settings["functions"]
    static_headers = {h["key"]: h["value"] for h in settings["headers"][0]["headers"]}
    assert static_headers == SECURITY_HEADERS


def test_vercelignore_keeps_secrets_out():
    ignored = (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored and ".venv/" in ignored
