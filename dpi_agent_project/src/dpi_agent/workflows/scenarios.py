"""
The three demonstration scenarios.

They always use the deterministic interpreter so the outcome is the same on
every machine, with or without a language model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dpi_agent.workflows.engine import StepCallback, process_request


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    national_id: str
    citizen: str
    description: str
    request: str


SCENARIOS = {
    "a": Scenario(
        key="a",
        title="Subsidy Success",
        national_id="765-741-434",
        citizen="Suresh Kaur",
        description="ACTIVE identity, ACTIVE agriculture consent, 12.8 acres of land",
        request=("I am Suresh Kaur, ID 765-741-434. I want to apply for a solar pump subsidy."),
    ),
    "b": Scenario(
        key="b",
        title="DEPA Consent Failure",
        national_id="213-163-577",
        citizen="Anjali Iyer",
        description="Consent allows agri_land only, so the credit request is denied",
        request=(
            "I am Anjali Iyer, ID 213-163-577. I want to assess my credit for an agricultural loan."
        ),
    ),
    "c": Scenario(
        key="c",
        title="Human Escalation",
        national_id="715-454-455",
        citizen="Suresh Mishra",
        description="SUSPENDED identity triggers escalation to a human reviewer",
        request=(
            "I am Suresh Mishra, ID 715-454-455. I need help, I'm not sure how to apply "
            "for a solar pump subsidy."
        ),
    ),
}


def run_scenario(key: str, on_step: StepCallback | None = None) -> dict[str, Any]:
    """Run scenario "a", "b" or "c" and return the workflow result."""
    scenario = SCENARIOS[key.lower()]
    return process_request(scenario.request, on_step=on_step)


def scenario_summaries() -> dict[str, dict[str, str]]:
    """Scenario metadata for the web UI."""
    return {
        f"scenario_{s.key}": {
            "national_id": s.national_id,
            "name": s.citizen,
            "description": f"{s.title} - {s.description}",
        }
        for s in SCENARIOS.values()
    }
