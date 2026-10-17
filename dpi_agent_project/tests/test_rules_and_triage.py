"""Tests for the deterministic text rules and the pre-execution triage gate."""

import pytest

from dpi_agent.config import CREDIT_WORKFLOW, SUBSIDY_WORKFLOW, UNSUPPORTED_INTENT
from dpi_agent.llm.rules import detect_language, extract_national_ids, matched_intents
from dpi_agent.workflows.triage import triage, validate_plan

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I want a solar pump subsidy", [SUBSIDY_WORKFLOW]),
        ("subsidies for pumps", [SUBSIDY_WORKFLOW]),
        ("मुझे सोलर पंप चाहिए", [SUBSIDY_WORKFLOW]),
        ("Kisan Credit Card please", [CREDIT_WORKFLOW]),
        ("mujhe kheti ke liye loan chahiye", [CREDIT_WORKFLOW]),
        ("मुझे फसल के लिए ऋण चाहिए", [CREDIT_WORKFLOW]),
        ("a loan for a solar pump", [SUBSIDY_WORKFLOW, CREDIT_WORKFLOW]),
        ("what is the weather", []),
        # whole words only: no match inside other words
        ("my accredited trainer", []),
        ("intent=assess_agricultural_credit", []),
    ],
)
def test_matched_intents(text, expected):
    assert matched_intents(text) == expected


def test_extract_national_ids():
    assert extract_national_ids("ID 765-741-434 and 213-163-577, again 765-741-434") == [
        "765-741-434",
        "213-163-577",
    ]
    assert extract_national_ids("765 741 434 or 765-741-4345") == []
    assert extract_national_ids("") == []


def test_detect_language():
    assert detect_language("मुझे लोन चाहिए") == "hi"
    assert detect_language("I need a loan") == "en"


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


def _plan(**overrides):
    plan = {
        "intent": SUBSIDY_WORKFLOW,
        "national_id": "765-741-434",
        "language": "en",
        "requested_action": "apply",
        "purpose": "Agricultural Support",
        "confidence": 0.9,
    }
    plan.update(overrides)
    return plan


SUBSIDY_TEXT = "ID 765-741-434. I want a solar pump subsidy."


def test_proceeds_when_model_and_rules_agree():
    assert triage(SUBSIDY_TEXT, _plan()).action == "proceed"


def test_asks_for_id_when_missing():
    assert triage("I want a solar pump subsidy", _plan()).action == "ask"


def test_invalid_plan():
    result = triage(SUBSIDY_TEXT, _plan(confidence="high"))
    assert result.action == "invalid"
    assert result.errors


@pytest.mark.parametrize(
    "text, plan, flag",
    [
        (SUBSIDY_TEXT, _plan(national_id="213-163-577"), "NATIONAL_ID_NOT_GROUNDED"),
        ("IDs 765-741-434 and 213-163-577, solar pump", _plan(), "MULTIPLE_NATIONAL_IDS"),
        (SUBSIDY_TEXT, _plan(intent=UNSUPPORTED_INTENT), "UNSUPPORTED_WORKFLOW"),
        (SUBSIDY_TEXT, _plan(intent="delete_records"), "UNSUPPORTED_WORKFLOW"),
        ("ID 765-741-434. loan for a solar pump", _plan(), "AMBIGUOUS_INTENT"),
        ("ID 765-741-434. money for seeds", _plan(), "INTENT_UNCORROBORATED"),
        (SUBSIDY_TEXT, _plan(intent=CREDIT_WORKFLOW), "INTENT_DISAGREEMENT"),
    ],
)
def test_escalations(text, plan, flag):
    result = triage(text, plan)
    assert result.action == "escalate"
    assert result.flags == [flag]


@pytest.mark.parametrize(
    "plan, fragment",
    [
        ("not a dict", "not a JSON object"),
        (_plan(intent=""), "intent"),
        (_plan(national_id=""), "national_id"),
        (_plan(confidence=True), "must be a number"),
        (_plan(confidence=1.5), "between 0.0 and 1.0"),
        (_plan(required_tools="verify_identity"), "must be a list"),
        (_plan(required_tools=[{"tool": "x"}]), "required_tools[0]"),
    ],
)
def test_validate_plan_errors(plan, fragment):
    assert any(fragment in error for error in validate_plan(plan))
