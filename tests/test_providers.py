"""Tests for the language-model providers, using a fake Ollama HTTP server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dpi_agent.config import SUBSIDY_WORKFLOW
from dpi_agent.llm.prompts import PLAN_SCHEMA
from dpi_agent.llm.providers import (
    DeterministicProvider,
    LLMUnavailable,
    OllamaModel,
    OllamaProvider,
    choose_model,
    get_provider,
    list_ollama_models,
)
from dpi_agent.workflows.engine import process_request

GOOD_PLAN = {
    "intent": SUBSIDY_WORKFLOW,
    "national_id": "765-741-434",
    "language": "en",
    "requested_action": "apply for solar pump subsidy",
    "purpose": "Agricultural Support",
    "confidence": 0.93,
}

TAGS = {
    "models": [
        {
            "name": "llama3.2:1b",
            "size": 1_300_000_000,
            "details": {"parameter_size": "1.2B", "family": "llama"},
        },
        {
            "name": "qwen2.5:7b",
            "size": 4_700_000_000,
            "details": {"parameter_size": "7.6B", "family": "qwen2"},
        },
        {
            "name": "nomic-embed-text:latest",
            "size": 270_000_000,
            "details": {"parameter_size": "137M", "family": "nomic-bert"},
        },
    ]
}


class FakeOllama:
    """A minimal stand-in for the Ollama API that records chat requests."""

    def __init__(self, replies):
        self.replies = list(replies)  # message contents to return, in order
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, body):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._reply(TAGS)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                self._reply({"message": {"role": "assistant", "content": fake.replies.pop(0)}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_ollama():
    servers = []

    def start(*replies):
        server = FakeOllama(replies)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

MODELS = [
    OllamaModel("llama3.2:1b", 1_300_000_000, "1.2B", "llama"),
    OllamaModel("qwen2.5:7b", 4_700_000_000, "7.6B", "qwen2"),
    OllamaModel("nomic-embed-text:latest", 270_000_000, "137M", "nomic-bert"),
    OllamaModel("mistral:latest", 4_100_000_000, "7.2B", "llama"),
]


def test_auto_picks_largest_chat_model():
    assert choose_model("auto", MODELS) == "qwen2.5:7b"


def test_auto_ignores_embedding_models():
    embed_only = [m for m in MODELS if m.is_embedding]
    with pytest.raises(LLMUnavailable, match="No chat models"):
        choose_model("auto", embed_only)


def test_explicit_model_and_latest_tag():
    assert choose_model("llama3.2:1b", MODELS) == "llama3.2:1b"
    assert choose_model("mistral", MODELS) == "mistral:latest"


def test_missing_model_explains_how_to_fix():
    with pytest.raises(LLMUnavailable, match="ollama pull qwen2.5-coder:7b"):
        choose_model("qwen2.5-coder:7b", MODELS)


def test_parameter_parsing():
    assert OllamaModel("m", 0, "1.2B", "").parameters == 1.2e9
    assert OllamaModel("m", 0, "137M", "").parameters == 137e6
    assert OllamaModel("m", 0, "", "").parameters == 0


# ---------------------------------------------------------------------------
# Ollama provider against the fake server
# ---------------------------------------------------------------------------


def test_lists_installed_models(fake_ollama):
    server = fake_ollama()
    names = [m.name for m in list_ollama_models(server.host)]
    assert names == ["llama3.2:1b", "qwen2.5:7b", "nomic-embed-text:latest"]


def test_interpret_sends_schema_constrained_chat(fake_ollama):
    server = fake_ollama(json.dumps(GOOD_PLAN))
    provider = OllamaProvider(model="auto", host=server.host)

    result = provider.interpret("ID 765-741-434. Solar pump subsidy please.")

    assert result.plan == GOOD_PLAN
    assert result.interpreter == "ollama:qwen2.5:7b"
    request = server.requests[0]
    assert request["model"] == "qwen2.5:7b"
    assert request["format"] == PLAN_SCHEMA
    assert request["stream"] is False
    assert request["options"]["temperature"] == 0
    assert request["messages"][0]["role"] == "system"
    assert request["messages"][-1] == {
        "role": "user",
        "content": "ID 765-741-434. Solar pump subsidy please.",
    }


def test_retries_once_on_invalid_json(fake_ollama):
    server = fake_ollama("not json", json.dumps(GOOD_PLAN))
    provider = OllamaProvider(model="llama3.2:1b", host=server.host)
    assert provider.interpret("ID 765-741-434 solar pump").plan == GOOD_PLAN
    assert len(server.requests) == 2


def test_gives_up_after_two_invalid_replies(fake_ollama):
    server = fake_ollama("nope", "still nope")
    provider = OllamaProvider(model="llama3.2:1b", host=server.host)
    with pytest.raises(LLMUnavailable, match="usable JSON"):
        provider.interpret("ID 765-741-434 solar pump")


def test_health_reports_selected_model(fake_ollama):
    server = fake_ollama()
    health = OllamaProvider(model="auto", host=server.host).health()
    assert health["available"] is True
    assert health["model"] == "qwen2.5:7b"
    assert "nomic-embed-text:latest" in health["installed_models"]


def test_unreachable_ollama():
    provider = OllamaProvider(model="auto", host="http://127.0.0.1:9", timeout=2)
    assert provider.health()["available"] is False
    with pytest.raises(LLMUnavailable, match="Cannot reach Ollama"):
        provider.interpret("ID 765-741-434 solar pump")


def test_workflow_falls_back_to_rules_when_model_is_down():
    provider = OllamaProvider(model="auto", host="http://127.0.0.1:9", timeout=2)
    result = process_request("ID 765-741-434. I want a solar pump subsidy.", provider=provider)
    assert result["status"] == "COMPLETED"
    assert result["interpreter"] == "deterministic"
    assert any("Model unavailable" in step["message"] for step in result["steps"])


def test_model_output_drives_workflow(fake_ollama):
    server = fake_ollama(json.dumps(GOOD_PLAN))
    provider = OllamaProvider(model="llama3.2:1b", host=server.host)
    result = process_request(
        "I am Suresh Kaur, ID 765-741-434. Solar pump subsidy.", provider=provider
    )
    assert result["status"] == "COMPLETED"
    assert result["interpreter"] == "ollama:llama3.2:1b"


def test_model_intent_disagreement_is_escalated(fake_ollama):
    """A model that calls a credit request a subsidy request is overruled."""
    server = fake_ollama(json.dumps(GOOD_PLAN))  # says subsidy
    provider = OllamaProvider(model="llama3.2:1b", host=server.host)
    result = process_request("ID 765-741-434. I need a loan for my farm.", provider=provider)
    assert result["status"] == "ESCALATED"
    assert "disagrees" in result["escalation"]["reason"]
    assert "transaction" not in result


# ---------------------------------------------------------------------------
# Factory and deterministic provider
# ---------------------------------------------------------------------------


def test_get_provider():
    assert isinstance(get_provider("deterministic"), DeterministicProvider)
    assert isinstance(get_provider("ollama"), OllamaProvider)
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_provider("gpt")


def test_deterministic_never_invents_an_id():
    plan = DeterministicProvider().interpret("I want a solar pump subsidy").plan
    assert plan["national_id"] == ""


# ---------------------------------------------------------------------------
# Groq (OpenAI-compatible) provider against a fake server
# ---------------------------------------------------------------------------

from dpi_agent.llm.providers import GroqProvider, rate_limit_wait_seconds  # noqa: E402

GROQ_MODELS = {
    "data": [
        {"id": "qwen/qwen3.8-27b", "active": True},
        {"id": "openai/gpt-oss-20b", "active": True},
        {"id": "whisper-large-v3", "active": True},
        {"id": "meta-llama/llama-prompt-guard-2-22m", "active": True},
        {"id": "canopylabs/orpheus-v1-english", "active": True},
        {"id": "retired-model", "active": False},
    ]
}


class FakeGroq:
    """Replies to /models and /chat/completions with a scripted list of (status, body, headers)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body, headers=None):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
                self._send(200, GROQ_MODELS)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
                )
                self._send(*fake.replies.pop(0))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/openai/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def ok(plan=None):
    content = json.dumps(plan or GOOD_PLAN)
    return (200, {"choices": [{"message": {"role": "assistant", "content": content}}]})


@pytest.fixture
def fake_groq():
    servers = []

    def start(*replies):
        server = FakeGroq(replies)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def groq(server, model="qwen/qwen3.8-27b", key="test-key"):
    return GroqProvider(model=model, api_key=key, base_url=server.base_url, timeout=5)


def test_groq_sends_strict_schema_with_bearer_key(fake_groq):
    server = fake_groq(ok())
    result = groq(server).interpret("ID 765-741-434. Solar pump subsidy please.")
    assert result.plan == GOOD_PLAN
    assert result.interpreter == "groq:qwen/qwen3.8-27b"
    request = server.requests[0]
    assert request["path"] == "/openai/v1/chat/completions"
    assert request["auth"] == "Bearer test-key"
    fmt = request["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == PLAN_SCHEMA
    assert request["body"]["temperature"] == 0
    assert "reasoning_effort" not in request["body"]


def test_groq_gpt_oss_uses_low_reasoning_effort(fake_groq):
    server = fake_groq(ok())
    groq(server, model="openai/gpt-oss-20b").interpret("ID 765-741-434 solar pump")
    assert server.requests[0]["body"]["reasoning_effort"] == "low"


def test_groq_falls_back_to_weaker_output_formats(fake_groq):
    unsupported = (
        400,
        {"error": {"message": "response_format json_schema is not supported by this model"}},
    )
    server = fake_groq(unsupported, unsupported, ok())
    provider = groq(server, model="allam-2-7b")
    assert provider.interpret("ID 765-741-434 solar pump").plan == GOOD_PLAN
    formats = [r["body"]["response_format"] for r in server.requests]
    assert [f["type"] for f in formats] == ["json_schema", "json_schema", "json_object"]
    assert formats[1]["json_schema"]["strict"] is False
    # the working format is remembered for the next request
    server.replies.append(ok())
    provider.interpret("ID 765-741-434 solar pump")
    assert server.requests[-1]["body"]["response_format"] == {"type": "json_object"}


def test_groq_waits_and_retries_on_short_rate_limit(fake_groq):
    server = fake_groq((429, {"error": {"message": "rate limited"}}, {"retry-after": "0.1"}), ok())
    assert groq(server).interpret("ID 765-741-434 solar pump").plan == GOOD_PLAN
    assert len(server.requests) == 2


def test_groq_gives_up_on_long_rate_limit(fake_groq):
    server = fake_groq((429, {"error": {"message": "rate limited"}}, {"retry-after": "120"}))
    with pytest.raises(LLMUnavailable, match="rate limit reached, try again in 120s"):
        groq(server).interpret("ID 765-741-434 solar pump")


def test_groq_bad_key_error_never_contains_the_key(fake_groq):
    server = fake_groq((401, {"error": {"message": "Invalid API Key"}}))
    with pytest.raises(LLMUnavailable) as error:
        groq(server, key="gsk_secret_value").interpret("ID 765-741-434 solar pump")
    assert "HTTP 401" in str(error.value)
    assert "gsk_secret_value" not in str(error.value)


def test_groq_lists_only_active_chat_models(fake_groq):
    server = fake_groq()
    assert groq(server).list_models() == ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]


def test_groq_health(fake_groq):
    server = fake_groq()
    health = groq(server).health()
    assert health["available"] is True and health["local"] is False
    missing = groq(server, model="llama-9000").health()
    assert missing["available"] is False and "qwen/qwen3.8-27b" in missing["error"]
    no_key = GroqProvider(api_key="", base_url=server.base_url).health()
    assert no_key["available"] is False and "GROQ_API_KEY" in no_key["error"]


def test_workflow_falls_back_to_rules_when_groq_fails(fake_groq):
    server = fake_groq((401, {"error": {"message": "Invalid API Key"}}))
    result = process_request("ID 765-741-434. I want a solar pump subsidy.", provider=groq(server))
    assert result["status"] == "COMPLETED"
    assert result["interpreter"] == "deterministic"


@pytest.mark.parametrize(
    "headers, seconds",
    [
        ({"retry-after": "2"}, 2.0),
        ({"x-ratelimit-reset-tokens": "7.6s"}, 7.6),
        ({"x-ratelimit-reset-requests": "1m26.4s"}, 86.4),
        ({"x-ratelimit-reset-tokens": "250ms"}, 0.25),
        ({}, None),
    ],
)
def test_rate_limit_wait_parsing(headers, seconds):
    result = rate_limit_wait_seconds(headers)
    assert result == pytest.approx(seconds) if seconds is not None else result is None


def test_get_provider_knows_groq():
    assert isinstance(get_provider("groq"), GroqProvider)
