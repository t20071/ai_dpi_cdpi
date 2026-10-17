"""
Tests against the real Groq API.

They use GROQ_API_KEY from the repository's .env file and count against your
Groq quota, so they only run when asked:

    pytest --live-groq
"""

from pathlib import Path

import pytest

from dpi_agent.llm.providers import GroqProvider
from dpi_agent.workflows.triage import triage, validate_plan

pytestmark = pytest.mark.groq


@pytest.fixture(scope="module")
def provider():
    env = Path(__file__).resolve().parents[1] / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    key = next((ln.split("=", 1)[1].strip() for ln in lines if ln.startswith("GROQ_API_KEY=")), "")
    if not key:
        pytest.skip("No GROQ_API_KEY in .env")
    return GroqProvider(api_key=key, timeout=60)


def test_groq_reply_matches_schema(provider):
    plan = provider.interpret("I am Suresh Kaur, ID 765-741-434. I want a solar pump subsidy.").plan
    assert validate_plan(plan) == []
    assert plan["national_id"] == "765-741-434"


def test_groq_hinglish_loan_request(provider):
    text = "Mera ID 213-163-577 hai, mujhe kheti ke liye loan chahiye"
    plan = provider.interpret(text).plan
    assert plan["intent"] == "assess_agricultural_credit"
    assert triage(text, plan).action == "proceed"
