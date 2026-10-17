"""
Shared test setup.

pytest imports this file before any test module, so these environment
variables are in place when dpi_agent.config is first imported:

- All runtime files (database, audit logs, outputs) go to a throwaway
  directory, so tests never touch data/runtime/.
- Every setting the tests depend on is pinned, so a personal .env cannot
  change results.  REFERENCE_TIME fixes "now" for consent-expiry checks.
- GROQ_API_KEY is blanked so no test calls the real Groq API by accident.
  Tests marked ``groq`` call it on purpose and only run with --live-groq.
"""

import os
import shutil
import tempfile

import pytest

_RUNTIME = tempfile.mkdtemp(prefix="dpi_agent_tests_")

os.environ.pop("VERCEL", None)
os.environ.update(
    {
        "DPI_RUNTIME_DIR": _RUNTIME,
        "REFERENCE_TIME": "2026-10-01T00:00:00+00:00",
        "LLM_PROVIDER": "deterministic",
        "OLLAMA_MODEL": "auto",
        "GROQ_API_KEY": "",
        "GROQ_MODEL": "qwen/qwen3.8-27b",
        "CONFIDENCE_THRESHOLD": "0.80",
        "HIGH_IMPACT_CONFIDENCE_THRESHOLD": "0.90",
        "MINIMUM_LAND_AREA_ACRES": "2.0",
        "MINIMUM_CREDIT_SCORE": "500",
        "RATE_LIMIT_PER_MINUTE": "10",
        "ALLOWED_HOSTS": "localhost,127.0.0.1,[::1]",
    }
)


def pytest_addoption(parser):
    parser.addoption(
        "--live-groq",
        action="store_true",
        help="also run tests that call the real Groq API (uses GROQ_API_KEY from .env)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live-groq"):
        return
    skip = pytest.mark.skip(reason="calls the real Groq API; run with --live-groq")
    for item in items:
        if "groq" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def database():
    """Import the CSV datasets once per test session, then clean up."""
    from dpi_agent.registries.database import ensure_database_ready

    ensure_database_ready()
    yield
    shutil.rmtree(_RUNTIME, ignore_errors=True)
