"""
Pre-execution triage: may this request start a workflow?

Runs after the model has interpreted the request and before any citizen
data is read.  It is a pure function (no I/O), so the evaluation harness
uses exactly the same logic as the live system.

Checks, in order:

1. Identity claim   the request must contain a national ID (else ask for it)
2. Plan schema      the interpretation has the required fields and types
3. ID grounding     the model's national_id is the one the citizen typed,
                    and the request names only one ID
4. Supported intent the intent is one of the supported workflows
5. Two-key intent   the deterministic keyword rules see the same intent the
                    model chose; disagreement or no supporting evidence is
                    sent to a human instead of executed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from dpi_agent.config import SUPPORTED_WORKFLOWS
from dpi_agent.llm.rules import extract_national_ids, matched_intents

Action = Literal["proceed", "ask", "invalid", "escalate"]


@dataclass(frozen=True)
class TriageResult:
    action: Action
    reason: str = ""
    flags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


ASK_FOR_ID = TriageResult(
    "ask",
    "Please include your national ID (format XXX-XXX-XXX) so your records can be verified.",
)


def check_identity_claim(text: str) -> TriageResult | None:
    """Step 1, before any model call: the citizen must state a national ID."""
    return None if extract_national_ids(text) else ASK_FOR_ID


def validate_plan(plan: Any) -> list[str]:
    """Step 2: return schema errors in the interpreted plan (empty if valid)."""
    if not isinstance(plan, dict):
        return ["Interpretation is not a JSON object"]

    errors = [
        f"Missing or empty required field: '{name}'"
        for name in ("intent", "national_id", "requested_action")
        if not str(plan.get(name) or "").strip()
    ]

    confidence = plan.get("confidence")
    if confidence is None:
        errors.append("Missing 'confidence' field")
    elif isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        errors.append(f"'confidence' must be a number, got {type(confidence).__name__}")
    elif not 0.0 <= confidence <= 1.0:
        errors.append(f"'confidence' must be between 0.0 and 1.0, got {confidence}")

    tools = plan.get("required_tools", [])
    if not isinstance(tools, list):
        errors.append("'required_tools' must be a list")
    else:
        for i, spec in enumerate(tools):
            if not isinstance(spec, dict) or "tool" not in spec or "arguments" not in spec:
                errors.append(f"required_tools[{i}] must be an object with 'tool' and 'arguments'")
    return errors


def triage(text: str, plan: dict[str, Any]) -> TriageResult:
    """Steps 1-5: decide whether the interpreted request may run."""
    if (missing_id := check_identity_claim(text)) is not None:
        return missing_id

    if errors := validate_plan(plan):
        return TriageResult(
            "invalid", "The request could not be interpreted reliably.", errors=errors
        )

    stated_ids = extract_national_ids(text)
    national_id = str(plan["national_id"]).strip()
    if len(stated_ids) > 1:
        return TriageResult(
            "escalate", "Request mentions more than one national ID", ["MULTIPLE_NATIONAL_IDS"]
        )
    if national_id != stated_ids[0]:
        return TriageResult(
            "escalate",
            "Model proposed a national ID the citizen did not provide",
            ["NATIONAL_ID_NOT_GROUNDED"],
        )

    intent = plan["intent"]
    if intent not in SUPPORTED_WORKFLOWS:
        return TriageResult(
            "escalate", f"Request is not a supported service ({intent})", ["UNSUPPORTED_WORKFLOW"]
        )

    rule_intents = matched_intents(text)
    if len(rule_intents) > 1:
        return TriageResult(
            "escalate", "Request mentions more than one service", ["AMBIGUOUS_INTENT"]
        )
    if not rule_intents:
        return TriageResult(
            "escalate",
            f"No keywords in the request support the model's intent ({intent})",
            ["INTENT_UNCORROBORATED"],
        )
    if intent != rule_intents[0]:
        return TriageResult(
            "escalate",
            f"Model intent ({intent}) disagrees with keyword rules ({rule_intents[0]})",
            ["INTENT_DISAGREEMENT"],
        )

    return TriageResult("proceed")
