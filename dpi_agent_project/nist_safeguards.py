"""
Sovereign DPI-AI Public Agent — NIST-Inspired Safeguards

Risk-gate and trustworthiness checks inspired by NIST AI RMF 1.0 concepts:
    GOVERN — governance and oversight
    MAP    — context and risk identification
    MEASURE — evaluation and measurement
    MANAGE  — risk treatment and response

IMPORTANT DISCLAIMERS:
- These checks do NOT constitute NIST certification or compliance.
- The 0.80 confidence threshold is an APPLICATION-SPECIFIC PROTOTYPE POLICY.
  It is NOT a threshold prescribed by NIST AI RMF.
- The bias/risk filter is a PROTOTYPE RISK-INDICATOR FILTER, not a
  comprehensive bias detector.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import (
    CONFIDENCE_THRESHOLD,
    HIGH_IMPACT_CONFIDENCE_THRESHOLD,
    ESCALATIONS_LOG,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# High-impact workflow classification (MAP function)
# ---------------------------------------------------------------------------

HIGH_IMPACT_WORKFLOWS = frozenset([
    "assess_agricultural_credit",
    "approve_loan",
    "disburse_funds",
])

# ---------------------------------------------------------------------------
# Prohibited / suspicious terms (prototype risk-indicator filter)
# ---------------------------------------------------------------------------

PROHIBITED_TERMS = [
    "guaranteed approval",
    "100% certain",
    "bypass consent",
    "skip verification",
    "override policy",
    "no risk",
    "hack",
    "exploit",
]

SUSPICIOUS_CERTAINTY_PHRASES = [
    "absolutely certain",
    "without any doubt",
    "guaranteed",
    "impossible to fail",
    "zero chance of",
]


# ---------------------------------------------------------------------------
# MEASURE: Confidence evaluation
# ---------------------------------------------------------------------------

def evaluate_confidence(
    confidence: float,
    workflow_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluate model confidence against the application-specific threshold.

    APPLICATION-SPECIFIC PROTOTYPE POLICY:
    - Standard threshold: 0.80 (configurable via CONFIDENCE_THRESHOLD)
    - High-impact threshold: 0.90 (configurable via HIGH_IMPACT_CONFIDENCE_THRESHOLD)

    These thresholds are NOT prescribed by NIST AI RMF.
    """
    threshold = CONFIDENCE_THRESHOLD
    is_high_impact = workflow_type in HIGH_IMPACT_WORKFLOWS

    if is_high_impact:
        threshold = HIGH_IMPACT_CONFIDENCE_THRESHOLD

    passed = confidence >= threshold

    return {
        "passed": passed,
        "confidence": confidence,
        "threshold": threshold,
        "is_high_impact": is_high_impact,
        "decision": "PASS" if passed else "FAIL_LOW_CONFIDENCE",
        "note": (
            "Application-specific prototype policy threshold. "
            "NOT prescribed by NIST AI RMF."
        ),
    }


# ---------------------------------------------------------------------------
# MAP: Ambiguity detection
# ---------------------------------------------------------------------------

def check_ambiguity(model_output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Check SLM output for ambiguity indicators.

    Flags:
    - Missing required fields
    - Empty or null values in critical fields
    - Multiple conflicting intents
    """
    flags: List[str] = []

    required_fields = ["intent", "national_id", "requested_action"]
    for field in required_fields:
        value = model_output.get(field)
        if not value or (isinstance(value, str) and not value.strip()):
            flags.append(f"MISSING_FIELD: {field}")

    # Check for confidence
    confidence = model_output.get("confidence")
    if confidence is None:
        flags.append("MISSING_CONFIDENCE")
    elif not isinstance(confidence, (int, float)):
        flags.append("INVALID_CONFIDENCE_TYPE")

    # Check for required_tools
    tools = model_output.get("required_tools", [])
    if not tools:
        flags.append("NO_TOOLS_REQUESTED")

    return {
        "passed": len(flags) == 0,
        "flags": flags,
        "decision": "PASS" if not flags else "FAIL_AMBIGUITY",
    }


# ---------------------------------------------------------------------------
# MAP: High-impact classification
# ---------------------------------------------------------------------------

def check_high_impact(workflow_type: str) -> Dict[str, Any]:
    """
    Classify whether a workflow is high-impact.

    High-impact workflows require:
    - Higher confidence threshold
    - Mandatory human review for borderline cases
    - Additional audit detail
    """
    is_high_impact = workflow_type in HIGH_IMPACT_WORKFLOWS

    return {
        "is_high_impact": is_high_impact,
        "workflow_type": workflow_type,
        "required_threshold": (
            HIGH_IMPACT_CONFIDENCE_THRESHOLD if is_high_impact
            else CONFIDENCE_THRESHOLD
        ),
        "human_review_required": is_high_impact,
    }


# ---------------------------------------------------------------------------
# MEASURE: Bias / risk indicator filter (PROTOTYPE)
# ---------------------------------------------------------------------------

def check_bias_risk(model_output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prototype risk-indicator filter.

    DISCLAIMER: This is NOT a comprehensive bias detector. It is a simple
    deterministic filter that checks for obvious risk indicators in SLM output.

    Checks for:
    - Unsupported claims
    - Prohibited/offensive terms
    - Suspicious certainty
    - Missing evidence for claims
    - Contradictory responses
    """
    flags: List[str] = []
    output_text = json.dumps(model_output).lower()

    # Check prohibited terms
    for term in PROHIBITED_TERMS:
        if term in output_text:
            flags.append(f"PROHIBITED_TERM: '{term}'")

    # Check suspicious certainty
    for phrase in SUSPICIOUS_CERTAINTY_PHRASES:
        if phrase in output_text:
            flags.append(f"SUSPICIOUS_CERTAINTY: '{phrase}'")

    # Check for very high confidence without tools (unsupported claim)
    confidence = model_output.get("confidence", 0)
    tools = model_output.get("required_tools", [])
    if confidence > 0.95 and len(tools) == 0:
        flags.append("HIGH_CONFIDENCE_NO_TOOLS: confidence > 0.95 with no tool verification")

    # Check for contradictory intent/action
    intent = model_output.get("intent", "")
    action = model_output.get("requested_action", "")
    if intent and action:
        # Very basic contradiction: intent says "deny" but action says "approve"
        if ("deny" in intent.lower() and "approve" in action.lower()) or \
           ("approve" in intent.lower() and "deny" in action.lower()):
            flags.append("CONTRADICTORY_INTENT_ACTION")

    return {
        "passed": len(flags) == 0,
        "flags": flags,
        "decision": "PASS" if not flags else "FAIL_RISK_INDICATORS",
        "disclaimer": "Prototype risk-indicator filter — not a comprehensive bias detector.",
    }


# ---------------------------------------------------------------------------
# MANAGE: Policy violation check
# ---------------------------------------------------------------------------

def check_policy_violations(
    model_output: Dict[str, Any],
    workflow_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Check for policy violations in the SLM output.

    Validates that:
    - Intent matches a supported workflow
    - No unsupported claims about eligibility
    - Required authorization steps are included
    """
    from config import SUPPORTED_WORKFLOWS, ALLOWED_TOOLS

    flags: List[str] = []

    # Check intent is supported
    intent = model_output.get("intent", "")
    if intent and intent not in SUPPORTED_WORKFLOWS:
        flags.append(f"UNSUPPORTED_WORKFLOW: {intent}")

    # Check tools are in allowlist
    tools = model_output.get("required_tools", [])
    for tool_spec in tools:
        tool_name = tool_spec.get("tool", "") if isinstance(tool_spec, dict) else str(tool_spec)
        if tool_name and tool_name not in ALLOWED_TOOLS:
            flags.append(f"UNAUTHORIZED_TOOL: {tool_name}")

    return {
        "passed": len(flags) == 0,
        "flags": flags,
        "decision": "PASS" if not flags else "FAIL_POLICY_VIOLATION",
    }


# ---------------------------------------------------------------------------
# Composite risk gate
# ---------------------------------------------------------------------------

def apply_risk_gate(
    model_output: Dict[str, Any],
    workflow_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Apply the composite NIST-inspired risk gate.

    Runs all checks and returns a combined decision:
    - PASS: all checks passed
    - FAIL: at least one check failed → triggers human escalation

    Inspired by NIST AI RMF functions but does NOT constitute NIST certification.
    """
    workflow_type = model_output.get("intent", "")
    confidence = model_output.get("confidence", 0.0)

    # Run all checks
    confidence_result = evaluate_confidence(confidence, workflow_type)
    ambiguity_result = check_ambiguity(model_output)
    high_impact_result = check_high_impact(workflow_type)
    bias_result = check_bias_risk(model_output)
    policy_result = check_policy_violations(model_output, workflow_context)

    # Aggregate flags
    all_flags: List[str] = []
    all_flags.extend(ambiguity_result.get("flags", []))
    all_flags.extend(bias_result.get("flags", []))
    all_flags.extend(policy_result.get("flags", []))

    if not confidence_result["passed"]:
        all_flags.append(
            f"LOW_CONFIDENCE: {confidence:.2f} < {confidence_result['threshold']:.2f}"
        )

    # Overall decision
    all_passed = (
        confidence_result["passed"]
        and ambiguity_result["passed"]
        and bias_result["passed"]
        and policy_result["passed"]
    )

    # High-impact workflows always get flagged for awareness
    if high_impact_result["is_high_impact"]:
        all_flags.append("HIGH_IMPACT_WORKFLOW: requires enhanced scrutiny")

    return {
        "passed": all_passed,
        "decision": "PASS" if all_passed else "FAIL",
        "confidence_check": confidence_result,
        "ambiguity_check": ambiguity_result,
        "high_impact_check": high_impact_result,
        "bias_risk_check": bias_result,
        "policy_check": policy_result,
        "all_flags": all_flags,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "NIST AI RMF-inspired risk assessment for prototype demonstration. "
            "Does NOT constitute NIST certification or compliance."
        ),
    }


# ---------------------------------------------------------------------------
# Human escalation
# ---------------------------------------------------------------------------

def create_escalation(
    case_id: str,
    national_id: str,
    user_request: str,
    workflow: str,
    model_output: Dict[str, Any],
    confidence: float,
    risk_flags: List[str],
    consent_status: str,
    decision: str,
    reason: str,
) -> Dict[str, Any]:
    """
    Create an escalation record for human review.

    Writes to audit/escalations_log.jsonl.

    The user-facing message clearly states that the request has been
    escalated for human review. No human reviewer is fabricated.
    """
    ESCALATIONS_LOG.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "case_id": case_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "national_id": national_id,
        "user_request": user_request,
        "workflow": workflow,
        "model_output_summary": {
            "intent": model_output.get("intent", ""),
            "confidence": model_output.get("confidence", 0),
            "requested_action": model_output.get("requested_action", ""),
        },
        "confidence": confidence,
        "risk_flags": risk_flags,
        "consent_status": consent_status,
        "decision": decision,
        "reason": reason,
    }

    with open(ESCALATIONS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    logger.warning(
        "ESCALATION created: case=%s, national_id=%s, reason=%s",
        case_id, national_id, reason,
    )

    return {
        "escalated": True,
        "case_id": case_id,
        "message": (
            "Your request could not be completed automatically. "
            "It has been escalated for human review."
        ),
        "reason": reason,
    }
