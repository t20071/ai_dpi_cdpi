"""
Tests against a real local Ollama server.

Skipped automatically when Ollama is not running or has no chat model, so the
suite still passes on machines (and CI) without one.  Run only these with:

    pytest -m ollama
"""

import pytest

from dpi_agent.llm.providers import LLMUnavailable, OllamaProvider
from dpi_agent.workflows.triage import validate_plan

pytestmark = pytest.mark.ollama


@pytest.fixture(scope="module")
def provider():
    candidate = OllamaProvider(model="auto", timeout=300)
    try:
        candidate.model  # noqa: B018 - resolves the model, raising if unavailable
    except LLMUnavailable as exc:
        pytest.skip(f"Ollama not available: {exc}")
    return candidate


def test_reply_matches_schema(provider):
    plan = provider.interpret("I am Suresh Kaur, ID 765-741-434. I want a solar pump subsidy.").plan
    assert validate_plan(plan) == []


def test_copies_national_id(provider):
    plan = provider.interpret("My national ID is 213-163-577. I need a farm loan.").plan
    assert plan["national_id"] == "213-163-577"
