"""
HTTP routes, independent of any web server.

``dispatch(Request) -> Response`` holds every route: the REST API and the
static website in public/.  Two thin adapters call it:

    web/server.py  local development server (Python standard library)
    web/wsgi.py    WSGI ``app`` for Vercel (api/index.py) or any WSGI server

API:
    GET  /api/health                 models, database and policy status
    POST /api/chat                   run a request {"query": ..., "provider": ...}
    POST /api/scenario/{a|b|c}       run a demonstration scenario
    GET  /api/audit/{type}           recent records (consent, workflow, escalations)
    GET  /api/audit/verify           check every audit log's hash chain
    GET  /api/registries/{name}      browse a registry table (paged, searchable)
"""

from __future__ import annotations

import json
import logging
import mimetypes
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from dpi_agent import __version__, config
from dpi_agent.audit import ALL_LOGS
from dpi_agent.config import DATABASE_PATH
from dpi_agent.llm.providers import LLMProvider, get_provider
from dpi_agent.registries.database import ensure_database_ready, mask_phone
from dpi_agent.workflows.engine import process_request
from dpi_agent.workflows.scenarios import SCENARIOS, run_scenario, scenario_summaries

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
MAX_QUERY_CHARS = 2000
MAX_SEARCH_CHARS = 100
MAX_PAGE_SIZE = 200

# The page loads only its own files: no third-party scripts, styles or fonts.
# vercel.json repeats these headers for files Vercel's CDN serves directly;
# tests/test_web.py checks that the two copies match.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)  # lower-case names
    body: bytes = b""
    client_ip: str = ""
    body_too_large: bool = False

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def param(self, name: str, default: str = "") -> str:
        return (self.query.get(name) or [default])[0]


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    def header_list(self) -> list[tuple[str, str]]:
        headers = {
            "Content-Type": self.content_type,
            "Content-Length": str(len(self.body)),
            **SECURITY_HEADERS,
            **self.headers,
        }
        return list(headers.items())


def json_response(data: Any, status: int = HTTPStatus.OK, **headers: str) -> Response:
    body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
    return Response(int(status), body, headers={"Cache-Control": "no-store", **headers})


def _error(status: int, message: str, **headers: str) -> Response:
    return json_response({"error": message}, status, **headers)


def parse_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    """Parse an integer, falling back to default and clamping to range."""
    try:
        value = int(raw) if raw not in (None, "") else default
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


# ---------------------------------------------------------------------------
# Providers available to the web UI
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """One provider instance per name, created on first use."""

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self.default_name = (
            config.LLM_PROVIDER if config.LLM_PROVIDER in config.PROVIDERS else "deterministic"
        )
        self._lock = threading.Lock()

    def get(self, name: str) -> LLMProvider:
        with self._lock:
            if name not in self._providers:
                self._providers[name] = get_provider(name)
            return self._providers[name]

    def use(self, name: str, provider: LLMProvider) -> None:
        """Make ``provider`` the default (CLI: dpi-agent serve --provider/--model)."""
        with self._lock:
            self._providers[name] = provider
            self.default_name = name

    def health(self) -> dict[str, dict[str, Any]]:
        return {name: self.get(name).health() for name in config.PROVIDERS}


providers = ProviderRegistry()


class RateLimiter:
    """Sliding one-minute window per client.  Per process, so best effort on serverless."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str) -> float:
        """Record a hit; return 0 if allowed, else seconds until the next slot."""
        if self.per_minute <= 0:
            return 0.0
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(client, deque())
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return 60 - (now - hits[0])
            hits.append(now)
            if len(self._hits) > 10_000:  # forget idle clients
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 60}
            return 0.0


model_rate_limiter = RateLimiter(config.RATE_LIMIT_PER_MINUTE)

_db_lock = threading.Lock()
_db_ready = False


def _ensure_database() -> None:
    global _db_ready
    if not _db_ready:
        with _db_lock:
            if not _db_ready:
                ensure_database_ready()
                _db_ready = True


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _host_allowed(request: Request) -> bool:
    if "*" in config.ALLOWED_HOSTS:
        return True
    host = request.header("host").lower()
    hostname = host if host.endswith("]") else host.rsplit(":", 1)[0]
    return hostname in config.ALLOWED_HOSTS


def _client_key(request: Request) -> str:
    """The client's IP.  Behind Vercel, its proxy headers carry the real address."""
    if config.IS_VERCEL:
        forwarded = request.header("x-real-ip") or request.header("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client_ip or "unknown"


def dispatch(request: Request) -> Response:
    """Route one request.  Never raises: failures become 500 responses."""
    try:
        if not _host_allowed(request):
            return _error(HTTPStatus.FORBIDDEN, "Host not allowed")
        if request.path.startswith("/api/") or request.path == "/api":
            return _api(request)
        if request.method in ("GET", "HEAD"):
            return _static(request.path)
        return _error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed", Allow="GET, HEAD")
    except Exception:  # noqa: BLE001 - never leak a traceback to the client
        logger.exception("Unhandled error for %s %s", request.method, request.path)
        return _error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")


def _api(request: Request) -> Response:
    parts = request.path.strip("/").split("/")[1:]  # drop "api"
    route = parts[0] if parts else ""
    arg = parts[1].lower() if len(parts) > 1 else ""

    if request.method == "GET":
        if route == "health" and not arg:
            return json_response(_health())
        if route == "audit" and arg == "verify":
            return json_response(_verify_audit())
        if route == "audit" and arg:
            if arg not in ALL_LOGS:
                return _error(HTTPStatus.NOT_FOUND, f"Unknown audit log '{arg}'")
            limit = parse_int(request.param("limit"), 50, 1, MAX_PAGE_SIZE)
            return json_response({"type": arg, "records": ALL_LOGS[arg].read(limit)})
        if route == "registries" and arg:
            _ensure_database()
            return json_response(
                query_registry(
                    arg,
                    search=request.param("q").strip()[:MAX_SEARCH_CHARS],
                    limit=parse_int(request.param("limit"), 25, 1, MAX_PAGE_SIZE),
                    offset=parse_int(request.param("offset"), 0, 0, 10**9),
                )
            )
        return _error(HTTPStatus.NOT_FOUND, f"Route not found: {request.path}")

    if request.method != "POST":
        return _error(HTTPStatus.METHOD_NOT_ALLOWED, "Use GET or POST", Allow="GET, POST")

    # Requiring application/json makes browsers send a CORS pre-flight for any
    # cross-site request, which this API never approves (it sends no CORS
    # headers).  Without it, a cross-site form post could run workflows.
    content_type = request.header("content-type").split(";")[0].strip().lower()
    if content_type != "application/json":
        return _error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
    if request.body_too_large:
        return _error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
    try:
        body = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _error(HTTPStatus.BAD_REQUEST, f"Invalid JSON payload: {exc}")
    if not isinstance(body, dict):
        return _error(HTTPStatus.BAD_REQUEST, "JSON body must be an object")

    if route == "chat" and not arg:
        return _chat(request, body)
    if route == "scenario" and arg:
        if arg not in SCENARIOS:
            return _error(HTTPStatus.NOT_FOUND, f"Unknown scenario '{arg}' (use a, b or c)")
        _ensure_database()
        return json_response(run_scenario(arg))
    return _error(HTTPStatus.NOT_FOUND, f"Route not found: {request.path}")


def _chat(request: Request, body: dict[str, Any]) -> Response:
    query = str(body.get("query", "")).strip()
    if not query:
        return _error(HTTPStatus.BAD_REQUEST, "Empty 'query' in request body")
    if len(query) > MAX_QUERY_CHARS:
        return _error(HTTPStatus.BAD_REQUEST, f"'query' exceeds {MAX_QUERY_CHARS} characters")

    name = str(body.get("provider") or "").lower()
    if not name:  # older clients send use_live_slm instead
        name = providers.default_name if body.get("use_live_slm", True) else "deterministic"
    if name not in config.PROVIDERS:
        return _error(HTTPStatus.BAD_REQUEST, f"Unknown provider '{name}'")

    if name != "deterministic":
        wait = model_rate_limiter.check(_client_key(request))
        if wait:
            return _error(
                HTTPStatus.TOO_MANY_REQUESTS,
                f"Too many AI requests. Try again in {wait:.0f} seconds, or use the rules-only mode.",
                **{"Retry-After": str(int(wait) + 1)},
            )

    _ensure_database()
    logger.info("Chat request via %s: %s", name, query[:80])
    return json_response(process_request(query, provider=providers.get(name)))


def _health() -> dict[str, Any]:
    database: dict[str, Any] = {"initialized": DATABASE_PATH.exists()}
    if database["initialized"]:
        try:
            database["tables"] = _table_counts()
        except sqlite3.Error as exc:
            database["error"] = str(exc)
    return {
        "system": "Sovereign DPI-AI Public Agent",
        "version": __version__,
        "deployment": "vercel" if config.IS_VERCEL else "local",
        "default_provider": providers.default_name,
        "providers": providers.health(),
        "database": database,
        "audit_records": {name: log.count() for name, log in ALL_LOGS.items()},
        "config": {
            "confidence_threshold": config.CONFIDENCE_THRESHOLD,
            "high_impact_confidence_threshold": config.HIGH_IMPACT_CONFIDENCE_THRESHOLD,
            "min_land_area_acres": config.MINIMUM_LAND_AREA_ACRES,
            "min_credit_score": config.MINIMUM_CREDIT_SCORE,
            "rate_limit_per_minute": config.RATE_LIMIT_PER_MINUTE,
        },
        "scenario_citizens": scenario_summaries(),
    }


def _verify_audit() -> dict[str, Any]:
    results = {}
    for name, log in ALL_LOGS.items():
        result = log.verify()
        results[name] = {
            "ok": result.ok,
            "records": result.records,
            "error": result.error,
            "line": result.line,
        }
    return {"ok": all(r["ok"] for r in results.values()), "logs": results}


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

REGISTRY_TABLES = {
    "identity": (
        "mosip_identity_registry",
        ["national_id", "full_name", "phone_number", "preferred_language", "identity_status"],
    ),
    "agriculture": (
        "agriculture_land_registry",
        [
            "land_record_id",
            "national_id",
            "survey_number",
            "district",
            "land_area_acres",
            "current_crop",
        ],
    ),
    "credit": (
        "mifos_credit_registry",
        [
            "national_id",
            "credit_score",
            "avg_monthly_turnover_inr",
            "repayment_history_status",
            "active_gst_billing_status",
        ],
    ),
    "consent": (
        "depa_consent_artefacts",
        [
            "consent_id",
            "national_id",
            "consent_purpose",
            "granular_permissions",
            "consent_status",
            "valid_until",
            "cryptographic_signature",
        ],
    ),
    "legacy": (
        "legacy_municipal_log",
        [
            "transaction_id",
            "national_id",
            "service_requested",
            "timestamp",
            "consent_token_used",
            "slm_confidence_score",
            "execution_status",
        ],
    ),
}


def _table_counts() -> dict[str, int]:
    conn = sqlite3.connect(str(DATABASE_PATH))
    try:
        return {
            name: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            for name, (table, _) in REGISTRY_TABLES.items()
        }
    finally:
        conn.close()


def query_registry(name: str, search: str = "", limit: int = 25, offset: int = 0) -> dict[str, Any]:
    """Page through a registry table, optionally filtering every column with LIKE."""
    if name not in REGISTRY_TABLES:
        return {"error": f"Unknown registry table '{name}'", "rows": [], "total": 0}
    table, columns = REGISTRY_TABLES[name]
    column_list = ", ".join(columns)
    where, params = "", []
    if search:
        where = " WHERE " + " OR ".join(f"{col} LIKE ?" for col in columns)
        params = [f"%{search}%"] * len(columns)

    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM {table}{where}", params).fetchone()[0]  # noqa: S608
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT {column_list} FROM {table}{where} LIMIT ? OFFSET ?",  # noqa: S608
                [*params, limit, offset],
            )
        ]
    finally:
        conn.close()

    if name == "identity":
        for row in rows:
            row["phone_number"] = mask_phone(row.get("phone_number"))
    return {"table": name, "total": total, "limit": limit, "offset": offset, "rows": rows}


# ---------------------------------------------------------------------------
# Static website
# ---------------------------------------------------------------------------


def _static(path: str, public_dir: Path | None = None) -> Response:
    root = (public_dir or config.PUBLIC_DIR).resolve()
    relative = unquote(path).lstrip("/") or "index.html"
    target = (root / relative).resolve()
    if target.is_dir():
        target = target / "index.html"
    if not target.is_relative_to(root) or not target.is_file():
        return Response(
            HTTPStatus.NOT_FOUND,
            b"<!doctype html><title>Not found</title><p>Page not found. <a href='/'>Home</a></p>",
            "text/html; charset=utf-8",
            {"Cache-Control": "no-store"},
        )
    content_type = (
        _CONTENT_TYPES.get(target.suffix.lower())
        or mimetypes.guess_type(target.name)[0]
        or "application/octet-stream"
    )
    cache = "no-cache" if target.suffix == ".html" else "public, max-age=300"
    return Response(HTTPStatus.OK, target.read_bytes(), content_type, {"Cache-Control": cache})
