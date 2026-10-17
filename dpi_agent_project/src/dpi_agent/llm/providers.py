"""
Language-model providers.

    OllamaProvider         a local model served by Ollama (default)
    GroqProvider           a cloud model through the Groq API (needs GROQ_API_KEY)
    DeterministicProvider  keyword rules, no model needed (offline / tests)

All turn a citizen's message into the same plan dict (see llm/prompts.py)
and report how long it took.  Select one with LLM_PROVIDER; choose models
with OLLAMA_MODEL ("auto" = largest installed chat model) and GROQ_MODEL.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from dpi_agent import config
from dpi_agent.config import CREDIT_WORKFLOW, SUBSIDY_WORKFLOW, UNSUPPORTED_INTENT
from dpi_agent.llm.prompts import PLAN_SCHEMA, build_messages
from dpi_agent.llm.rules import detect_language, extract_national_ids, matched_intents

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """The model could not be reached or could not produce a usable answer."""


@dataclass(frozen=True)
class Interpretation:
    """A provider's reading of one citizen message."""

    plan: dict[str, Any]
    interpreter: str  # e.g. "deterministic" or "ollama:llama3.2:1b"
    latency_ms: float


class LLMProvider(ABC):
    """Common interface for anything that interprets citizen requests."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def _plan(self, text: str) -> dict[str, Any]: ...

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    def interpret(self, text: str) -> Interpretation:
        start = time.perf_counter()
        plan = self._plan(text)
        return Interpretation(plan, self.name, (time.perf_counter() - start) * 1000)


# ---------------------------------------------------------------------------
# Deterministic provider
# ---------------------------------------------------------------------------

_RULE_PLANS = {
    SUBSIDY_WORKFLOW: ("assess_and_submit", "Agricultural Support", 0.91),
    CREDIT_WORKFLOW: ("assess_credit", "Credit Assessment", 0.88),
    UNSUPPORTED_INTENT: ("unknown", "General Enquiry", 0.30),
}


class DeterministicProvider(LLMProvider):
    """Keyword-rule interpreter.  Needs no model and always gives the same answer."""

    @property
    def name(self) -> str:
        return "deterministic"

    def _plan(self, text: str) -> dict[str, Any]:
        ids = extract_national_ids(text)
        intents = matched_intents(text)
        intent = intents[0] if intents else UNSUPPORTED_INTENT
        action, purpose, confidence = _RULE_PLANS[intent]
        if len(intents) > 1:
            confidence = 0.5  # keywords for more than one service
        return {
            "intent": intent,
            "national_id": ids[0] if ids else "",
            "language": detect_language(text),
            "requested_action": action,
            "purpose": purpose,
            "confidence": confidence,
        }

    def health(self) -> dict[str, Any]:
        return {"available": True, "provider": "deterministic", "model": None, "local": True}


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OllamaModel:
    name: str
    size_bytes: int
    parameter_size: str  # as reported by Ollama, e.g. "1.2B"
    family: str

    @property
    def is_embedding(self) -> bool:
        return "embed" in self.name.lower() or self.family.lower() in {"bert", "nomic-bert"}

    @property
    def parameters(self) -> float:
        """Parameter count as a number, for ranking (0 if unknown)."""
        match = re.fullmatch(r"([\d.]+)\s*([KMBT]?)", self.parameter_size.strip().upper())
        if not match:
            return 0.0
        scale = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[match.group(2)]
        return float(match.group(1)) * scale


def _request_json(url: str, payload: dict | None, timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


_OLLAMA_IN_CLOUD = (
    "Ollama runs on your own computer, so it is not available in the cloud deployment. "
    "Use Groq there, or run the app locally."
)


def list_ollama_models(host: str = config.OLLAMA_HOST, timeout: float = 5) -> list[OllamaModel]:
    """Return the models installed in Ollama.  Raises LLMUnavailable if it is not running."""
    if config.IS_VERCEL and any(name in host for name in ("localhost", "127.0.0.1", "[::1]")):
        raise LLMUnavailable(_OLLAMA_IN_CLOUD)
    try:
        tags = _request_json(f"{host}/api/tags", None, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMUnavailable(
            f"Cannot reach Ollama at {host}. Start the Ollama app or run `ollama serve`."
        ) from exc
    return [
        OllamaModel(
            name=m.get("name", ""),
            size_bytes=int(m.get("size", 0)),
            parameter_size=m.get("details", {}).get("parameter_size", ""),
            family=m.get("details", {}).get("family", ""),
        )
        for m in tags.get("models", [])
    ]


def choose_model(requested: str, installed: list[OllamaModel]) -> str:
    """
    Resolve the model to use.

    "auto" (or empty) picks the largest installed chat model: bigger models
    follow the instructions more reliably.  An explicit name must be installed
    ("llama3.2" matches "llama3.2:latest").
    """
    requested = (requested or "auto").strip()
    if requested.lower() == "auto":
        chat_models = [m for m in installed if not m.is_embedding]
        if not chat_models:
            raise LLMUnavailable(
                "No chat models are installed in Ollama. Install one, for example "
                "`ollama pull llama3.2:3b`."
            )
        best = max(chat_models, key=lambda m: (m.parameters, m.size_bytes))
        return best.name

    names = {m.name for m in installed}
    for candidate in (requested, f"{requested}:latest"):
        if candidate in names:
            return candidate
    available = ", ".join(sorted(names)) or "none"
    raise LLMUnavailable(
        f"Ollama model '{requested}' is not installed (installed: {available}). "
        f"Run `ollama pull {requested}` or set OLLAMA_MODEL=auto."
    )


class OllamaProvider(LLMProvider):
    """A local model served by Ollama, constrained to PLAN_SCHEMA."""

    def __init__(
        self,
        model: str | None = None,
        host: str = config.OLLAMA_HOST,
        timeout: float = config.OLLAMA_TIMEOUT_SECONDS,
    ) -> None:
        self.host = host.rstrip("/")
        self.requested_model = model or config.OLLAMA_MODEL
        self.timeout = timeout
        self._model: str | None = None

    @property
    def model(self) -> str:
        """The resolved model name (looked up once, on first use)."""
        if self._model is None:
            installed = list_ollama_models(self.host, timeout=min(self.timeout, 5))
            self._model = choose_model(self.requested_model, installed)
        return self._model

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def _plan(self, text: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": build_messages(text),
            "format": PLAN_SCHEMA,
            "stream": False,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": {"temperature": 0, "seed": 42},
        }
        last_error: Exception | None = None
        for _ in range(2):  # one retry: a malformed reply is rare with a schema
            try:
                reply = _request_json(f"{self.host}/api/chat", payload, self.timeout)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
                raise LLMUnavailable(f"Ollama returned HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise LLMUnavailable(f"Ollama request failed: {exc}") from exc
            content = reply.get("message", {}).get("content", "")
            try:
                plan = json.loads(content)
            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning("Model returned invalid JSON, retrying: %r", content[:200])
                continue
            if isinstance(plan, dict):
                return plan
            last_error = ValueError("model reply is not a JSON object")
        raise LLMUnavailable(f"Model did not return a usable JSON plan: {last_error}")

    def health(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "provider": "ollama",
            "host": self.host,
            "requested_model": self.requested_model,
            "local": True,
        }
        try:
            installed = list_ollama_models(self.host, timeout=min(self.timeout, 5))
        except LLMUnavailable as exc:
            return {**status, "available": False, "model": None, "error": str(exc)}
        status["installed_models"] = [m.name for m in installed]
        try:
            self._model = choose_model(self.requested_model, installed)
        except LLMUnavailable as exc:
            return {**status, "available": False, "model": None, "error": str(exc)}
        return {**status, "available": True, "model": self._model}


# ---------------------------------------------------------------------------
# OpenAI-compatible providers (Groq)
# ---------------------------------------------------------------------------

# Response formats, strongest first.  "strict" is constrained decoding (the
# reply always matches PLAN_SCHEMA); "schema" is best-effort schema matching;
# "json" only guarantees a JSON object.  A model that rejects one format is
# moved to the next, and triage validates whatever comes back.
_RESPONSE_FORMATS = ("strict", "schema", "json")

# Model ids that are not chat models: speech-to-text, text-to-speech, safety
# classifiers and embeddings.
_NON_CHAT_HINTS = ("whisper", "orpheus", "tts", "guard", "embed")

_DURATION = re.compile(
    r"(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m(?!s))?(?:(?P<s>[\d.]+)s)?(?:(?P<ms>[\d.]+)ms)?"
)


def _duration_seconds(text: str) -> float | None:
    """Parse durations such as "7.6s", "1m26.4s" or "250ms"."""
    match = _DURATION.fullmatch(text.strip())
    if not match or not any(match.groupdict().values()):
        return None
    parts = {k: float(v) for k, v in match.groupdict().items() if v}
    return (
        parts.get("h", 0) * 3600
        + parts.get("m", 0) * 60
        + parts.get("s", 0)
        + parts.get("ms", 0) / 1000
    )


def rate_limit_wait_seconds(headers: Any) -> float | None:
    """How long a 429 response asks the client to wait, if it says."""
    retry_after = headers.get("retry-after") if headers else None
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    for name in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        value = headers.get(name) if headers else None
        if value and (seconds := _duration_seconds(value)) is not None:
            return seconds
    return None


class OpenAICompatibleProvider(LLMProvider):
    """
    A model behind an OpenAI-compatible chat-completions API.

    Groq is configured below; LM Studio, llama.cpp's server or vLLM expose the
    same API and could be added the same way to run a small model locally.
    """

    label = "openai-compatible"
    key_setting = "API key"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 30,
        max_retry_wait: float = 10,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retry_wait = max_retry_wait
        self._format_index = 0
        self._models_cache: tuple[float, list[str]] | None = None

    @property
    def name(self) -> str:
        return f"{self.label}:{self.model}"

    def _request(self, path: str, payload: dict | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "dpi-agent",
            },
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def _payload(self, text: str, response_format: str) -> dict[str, Any]:
        schema = {"name": "intake_plan", "schema": PLAN_SCHEMA}
        formats = {
            "strict": {"type": "json_schema", "json_schema": {**schema, "strict": True}},
            "schema": {"type": "json_schema", "json_schema": {**schema, "strict": False}},
            "json": {"type": "json_object"},
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": build_messages(text),
            "temperature": 0,
            "response_format": formats[response_format],
        }
        if "gpt-oss" in self.model:
            payload["reasoning_effort"] = "low"  # interpretation needs little reasoning
        return payload

    def _plan(self, text: str) -> dict[str, Any]:
        if not self.api_key:
            raise LLMUnavailable(f"{self.label} is not configured: {self.key_setting} is not set.")
        retries = 0
        while retries <= 3:
            response_format = _RESPONSE_FORMATS[self._format_index]
            try:
                reply = self._request("/chat/completions", self._payload(text, response_format))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                if exc.code == 429:
                    wait = rate_limit_wait_seconds(exc.headers)
                    if wait is not None and wait <= self.max_retry_wait and retries < 2:
                        logger.info("%s rate limited, retrying in %.1fs", self.label, wait)
                        time.sleep(wait + 0.25)
                        retries += 1
                        continue
                    when = f"in {wait:.0f}s" if wait is not None else "later"
                    raise LLMUnavailable(
                        f"{self.label} rate limit reached, try again {when}."
                    ) from exc
                if exc.code == 400 and "json_validate_failed" in detail and retries < 1:
                    retries += 1  # best-effort schema mode produced a mismatch
                    continue
                if (
                    exc.code == 400
                    and any(k in detail for k in ("response_format", "json_schema", "strict"))
                    and self._format_index < len(_RESPONSE_FORMATS) - 1
                ):
                    self._format_index += 1
                    logger.info(
                        "%s: falling back to %s output",
                        self.name,
                        _RESPONSE_FORMATS[self._format_index],
                    )
                    continue
                if exc.code == 401:
                    raise LLMUnavailable(
                        f"{self.label} rejected the API key (HTTP 401). Check {self.key_setting}."
                    ) from exc
                raise LLMUnavailable(f"{self.label} returned HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise LLMUnavailable(f"{self.label} request failed: {exc}") from exc

            content = (reply.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            try:
                plan = json.loads(content)
            except json.JSONDecodeError:
                plan = None
            if isinstance(plan, dict):
                return plan
            retries += 1
            logger.warning("%s returned an unusable reply, retrying: %r", self.name, content[:200])
        raise LLMUnavailable(f"{self.name} did not return a usable JSON plan.")

    def list_models(self, max_age: float = 300) -> list[str]:
        """Chat models this key can use (cached for ``max_age`` seconds)."""
        if self._models_cache and time.monotonic() - self._models_cache[0] < max_age:
            return self._models_cache[1]
        if not self.api_key:
            raise LLMUnavailable(f"{self.label} is not configured: {self.key_setting} is not set.")
        try:
            data = self._request("/models")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise LLMUnavailable(
                    f"{self.label} rejected the API key (HTTP 401). Check {self.key_setting}."
                ) from exc
            raise LLMUnavailable(f"{self.label} returned HTTP {exc.code} listing models.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMUnavailable(f"Cannot reach {self.label} at {self.base_url}: {exc}") from exc
        models = sorted(
            m["id"]
            for m in data.get("data", [])
            if m.get("active", True) and not any(h in m["id"].lower() for h in _NON_CHAT_HINTS)
        )
        self._models_cache = (time.monotonic(), models)
        return models

    def health(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "provider": self.label,
            "host": self.base_url,
            "requested_model": self.model,
            "local": False,
        }
        try:
            models = self.list_models()
        except LLMUnavailable as exc:
            return {**status, "available": False, "model": None, "error": str(exc)}
        status["installed_models"] = models
        if self.model not in models:
            error = f"Model '{self.model}' is not available on {self.label}. Choose one of: {', '.join(models)}"
            return {**status, "available": False, "model": None, "error": error}
        return {**status, "available": True, "model": self.model}


class GroqProvider(OpenAICompatibleProvider):
    """A cloud model through the Groq API.  The citizen's message is sent to Groq."""

    label = "groq"
    key_setting = "GROQ_API_KEY"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(
            model=model or config.GROQ_MODEL,
            base_url=base_url or config.GROQ_BASE_URL,
            api_key=config.GROQ_API_KEY if api_key is None else api_key,
            timeout=timeout or config.GROQ_TIMEOUT_SECONDS,
            max_retry_wait=config.GROQ_MAX_RETRY_WAIT_SECONDS,
        )


# ---------------------------------------------------------------------------
# Factory and process-wide default
# ---------------------------------------------------------------------------


def get_provider(name: str | None = None, model: str | None = None) -> LLMProvider:
    """Create a provider by name: "ollama", "groq" or "deterministic"."""
    name = (name or config.LLM_PROVIDER).strip().lower()
    if name == "ollama":
        return OllamaProvider(model=model)
    if name == "groq":
        return GroqProvider(model=model)
    if name == "deterministic":
        return DeterministicProvider()
    raise ValueError(f"Unknown LLM_PROVIDER '{name}'. Use one of: {', '.join(config.PROVIDERS)}.")


_default_provider: LLMProvider | None = None


def get_default_provider() -> LLMProvider:
    """The provider used for live requests (created from config on first use)."""
    global _default_provider
    if _default_provider is None:
        _default_provider = get_provider()
    return _default_provider


def set_default_provider(provider: LLMProvider) -> None:
    """Replace the provider used for live requests (CLI --model, tests)."""
    global _default_provider
    _default_provider = provider
