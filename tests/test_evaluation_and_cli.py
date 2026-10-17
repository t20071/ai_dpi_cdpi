"""Tests for the evaluation harness and the command-line interface."""

import json

import pytest

from dpi_agent import config
from dpi_agent.cli import main
from dpi_agent.evaluation import evaluate, format_comparison, load_cases
from dpi_agent.llm.providers import DeterministicProvider

# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def test_eval_cases_are_well_formed():
    cases = load_cases()
    assert len(cases) >= 25
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert case["expected_outcome"] in {"proceed", "ask", "hold"}


def test_deterministic_rules_on_eval_set():
    report = evaluate(DeterministicProvider(), load_cases())
    summary = report["summary"]
    assert summary["unsafe"] == 0
    assert summary["outcome_acc"] == 1.0
    assert summary["schema_valid"] == 1.0
    assert summary["id_acc"] == 1.0


def test_unsafe_actions_are_counted():
    """A provider that always says 'subsidy' must be caught on credit requests."""

    class AlwaysSubsidy(DeterministicProvider):
        @property
        def name(self):
            return "always-subsidy"

        def _plan(self, text):
            plan = super()._plan(text)
            plan["intent"] = config.SUBSIDY_WORKFLOW
            return plan

    report = evaluate(AlwaysSubsidy(), load_cases())
    # The two-key rule holds every wrong guess for a human, so nothing unsafe runs
    assert report["summary"]["unsafe"] == 0
    assert report["summary"]["intent_acc"] < 1.0
    assert report["summary"]["over_held"] > 0


def test_format_comparison():
    report = evaluate(DeterministicProvider(), load_cases()[:3])
    table = format_comparison([report])
    assert "deterministic" in table
    assert "unsafe" in table


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_ask_offline(capsys):
    code = main(
        ["ask", "I am Suresh Kaur, ID 765-741-434. Solar pump subsidy please.", "--offline"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "RESULT: COMPLETED" in out
    assert "[DEPA]" in out


def test_cli_ask_json(capsys):
    code = main(["ask", "solar pump subsidy", "--offline", "--json"])
    result = json.loads(capsys.readouterr().out.split("\n", 2)[2])
    assert code == 1
    assert result["status"] == "NEEDS_NATIONAL_ID"


def test_cli_offline_before_command(capsys):
    """Options given before the command name are not lost."""
    assert main(["--offline", "ask", "ID 765-741-434 solar pump subsidy"]) == 0
    assert "Interpreted by: deterministic" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_cli_scenarios(name, capsys):
    assert main(["scenario", name]) == 0
    assert f"SCENARIO {name.upper()}" in capsys.readouterr().out


def test_cli_audit_verify(capsys):
    main(["scenario", "a"])
    assert main(["audit", "verify"]) == 0
    assert "chain intact" in capsys.readouterr().out


def test_cli_eval_offline(capsys):
    assert main(["eval", "--offline"]) == 0
    out = capsys.readouterr().out
    assert "deterministic" in out
    assert list(config.EVAL_RESULTS_DIR.glob("*_deterministic.json"))
