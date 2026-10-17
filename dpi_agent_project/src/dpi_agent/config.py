"""
Central configuration.

Every setting can be overridden with an environment variable or a ``.env``
file in the repository root (see ``.env.example``).  Real environment
variables take precedence over ``.env``.

Disclaimers:
- This is a LOCAL PROTOTYPE, not a production government system.
- CONFIDENCE_THRESHOLD (0.80) is an application-specific prototype policy.
  It is NOT a threshold prescribed by NIST AI RMF.
- Policy rules (e.g. minimum land area) are demonstration values.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

# src/dpi_agent/config.py -> parents[2] is the repository root
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(env_path: Path) -> None:
    """Load KEY=VALUE lines from a .env file without overriding real env vars."""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(REPO_ROOT / ".env")


def _env_path(name: str, default: Path) -> Path:
    """A path from the environment; relative paths are taken from the repo root."""
    value = os.getenv(name, "").strip()
    if not value:
        return default
    path = Path(value).expanduser()
    return (path if path.is_absolute() else REPO_ROOT / path).resolve()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# data/raw      source CSV datasets (read-only inputs, committed to git)
# data/runtime  everything the app writes (database, audit logs, outputs);
#               safe to delete, recreated on next run

DATA_DIR = _env_path("DPI_DATA_DIR", REPO_ROOT / "data")
RAW_DATA_DIR = DATA_DIR / "raw"
RUNTIME_DIR = _env_path("DPI_RUNTIME_DIR", DATA_DIR / "runtime")

DATABASE_PATH = RUNTIME_DIR / "simulated_dpi.db"
AUDIT_DIRECTORY = RUNTIME_DIR / "audit"
LEGACY_OUTPUT_CSV = RUNTIME_DIR / "legacy_municipal_log_output.csv"
EVAL_RESULTS_DIR = RUNTIME_DIR / "evals"

CONSENT_AUDIT_LOG = AUDIT_DIRECTORY / "consent_audit_log.jsonl"
WORKFLOW_AUDIT_LOG = AUDIT_DIRECTORY / "workflow_audit_log.jsonl"
ESCALATIONS_LOG = AUDIT_DIRECTORY / "escalations_log.jsonl"

CSV_FILES = {
    "mosip_identity_registry": RAW_DATA_DIR / "mosip_identity_registry.csv",
    "agriculture_land_registry": RAW_DATA_DIR / "agriculture_land_registry.csv",
    "mifos_credit_registry": RAW_DATA_DIR / "mifos_credit_registry.csv",
    "depa_consent_artefacts": RAW_DATA_DIR / "depa_consent_artefacts.csv",
    "legacy_municipal_log": RAW_DATA_DIR / "legacy_municipal_log.csv",
}

# The website (HTML, CSS, JS, images).  Served by the local server, and by
# Vercel's CDN when deployed.
PUBLIC_DIR = REPO_ROOT / "public"

# ---------------------------------------------------------------------------
# Language model
# ---------------------------------------------------------------------------
# LLM_PROVIDER: the default interpreter
#   "ollama"        a local model through Ollama (data stays on this machine)
#   "groq"          a cloud model through the Groq API (needs GROQ_API_KEY;
#                   the citizen's message is sent to Groq)
#   "deterministic" keyword rules only, no language model
# OLLAMA_MODEL: an installed model name, or "auto" to pick the largest
#               installed chat model (run `dpi-agent models` to see them)

PROVIDERS = ("deterministic", "ollama", "groq")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "auto").strip()
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "10m")

# Groq exposes an OpenAI-compatible API.  The default model supports strict
# structured outputs (constrained decoding), like Ollama's `format`.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
GROQ_TIMEOUT_SECONDS = float(os.getenv("GROQ_TIMEOUT_SECONDS", "30"))
# On a rate limit (HTTP 429), wait and retry only if Groq asks for at most this long
GROQ_MAX_RETRY_WAIT_SECONDS = float(os.getenv("GROQ_MAX_RETRY_WAIT_SECONDS", "10"))

# ---------------------------------------------------------------------------
# Web server and deployment
# ---------------------------------------------------------------------------
# Vercel sets VERCEL=1 in its build and runtime environments.

IS_VERCEL = bool(os.getenv("VERCEL"))

# Host header values the server answers to.  Locally only loopback names are
# accepted (blocks DNS rebinding); a public deployment accepts any host.
# Comma separated, or "*" for any.
ALLOWED_HOSTS = [
    h.strip().lower()
    for h in os.getenv("ALLOWED_HOSTS", "*" if IS_VERCEL else "localhost,127.0.0.1,[::1]").split(
        ","
    )
    if h.strip()
]

# Requests per minute, per client IP, that may use a language model
# (Ollama or Groq).  0 turns the limit off.  Protects the Groq quota on a
# public deployment.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

# ---------------------------------------------------------------------------
# Risk and policy thresholds (prototype values, not real government policy)
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.80"))
HIGH_IMPACT_CONFIDENCE_THRESHOLD = float(os.getenv("HIGH_IMPACT_CONFIDENCE_THRESHOLD", "0.90"))
MINIMUM_LAND_AREA_ACRES = float(os.getenv("MINIMUM_LAND_AREA_ACRES", "2.0"))
MINIMUM_CREDIT_SCORE = int(os.getenv("MINIMUM_CREDIT_SCORE", "500"))

# ---------------------------------------------------------------------------
# Reference clock
# ---------------------------------------------------------------------------
# Consent expiry is checked against the current UTC time.  The bundled dataset
# has fixed valid_until dates, so demo outcomes drift as real time passes.
# Set REFERENCE_TIME (ISO 8601, e.g. 2026-10-01T00:00:00+00:00) to pin "now".

REFERENCE_TIME = os.getenv("REFERENCE_TIME", "").strip()


def utc_now() -> datetime:
    """Return the current UTC time, or REFERENCE_TIME when it is set."""
    if REFERENCE_TIME:
        pinned = datetime.fromisoformat(REFERENCE_TIME)
        return pinned if pinned.tzinfo else pinned.replace(tzinfo=UTC)
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Workflows, tools and consent purposes
# ---------------------------------------------------------------------------

SUBSIDY_WORKFLOW = "apply_for_solar_pump_subsidy"
CREDIT_WORKFLOW = "assess_agricultural_credit"
UNSUPPORTED_INTENT = "unsupported"

SUPPORTED_WORKFLOWS = frozenset([SUBSIDY_WORKFLOW, CREDIT_WORKFLOW])

# Tools are fixed per workflow.  The model chooses the intent; it never
# chooses which tools run.
WORKFLOW_TOOL_PLANS = {
    SUBSIDY_WORKFLOW: [
        "verify_identity",
        "check_consent",
        "fetch_land_record",
        "evaluate_subsidy_eligibility",
        "risk_check",
        "create_legacy_transaction",
    ],
    CREDIT_WORKFLOW: [
        "verify_identity",
        "check_consent",
        "assess_credit_profile",
        "risk_check",
        "create_legacy_transaction",
    ],
}

ALLOWED_TOOLS = frozenset(tool for plan in WORKFLOW_TOOL_PLANS.values() for tool in plan)

# Consent purposes acceptable for each workflow (DEPA purpose limitation)
WORKFLOW_CONSENT_PURPOSES = {
    SUBSIDY_WORKFLOW: ["Agricultural Support", "Subsidy Screening"],
    CREDIT_WORKFLOW: ["Credit Assessment", "Micro-Loan Processing", "Subsidy Screening"],
}
