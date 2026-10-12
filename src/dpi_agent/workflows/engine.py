"""
DPI workflow engine.

Every request follows the same deterministic pipeline, which the model
cannot bypass:

    identity claim -> model interpretation -> triage (schema, ID grounding,
    two-key intent) -> identity verification -> DEPA consent -> AI Blocks ->
    policy / eligibility -> NIST-inspired risk gate -> human escalation or
    +1 legacy write-back -> audit -> response

The model proposes.  The workflow decides.  The DPI tools execute.
The policy engine authorizes.  The audit layer records.
A human handles uncertain and high-impact cases.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from dpi_agent.audit import workflow_log
from dpi_agent.config import (
    CONFIDENCE_THRESHOLD,
    CREDIT_WORKFLOW,
    MINIMUM_CREDIT_SCORE,
    MINIMUM_LAND_AREA_ACRES,
    SUBSIDY_WORKFLOW,
    WORKFLOW_TOOL_PLANS,
)
from dpi_agent.governance.consent import check_agriculture_consent, check_credit_consent
from dpi_agent.governance.safeguards import apply_risk_gate, create_escalation
from dpi_agent.llm.providers import DeterministicProvider, LLMProvider
from dpi_agent.registries.database import fetch_credit_profile, fetch_land_record, verify_identity
from dpi_agent.registries.legacy_adapter import create_legacy_transaction
from dpi_agent.tools import execute_tool
from dpi_agent.workflows.triage import check_identity_claim, triage

logger = logging.getLogger(__name__)

StepCallback = Callable[[dict[str, Any]], None]


class _Stop(Exception):
    """Ends a workflow early (denial, escalation, missing data) with its result."""

    def __init__(self, result: dict[str, Any], final_status: str) -> None:
        super().__init__(final_status)
        self.result = result
        self.final_status = final_status


# Machine-readable stage for each step label (the web UI's progress tracker)
STAGES = {
    "PUBLIC AGENT": "interpret",
    "TRIAGE": "triage",
    "DPI WORKFLOW": "identity",
    "DEPA": "consent",
    "AI BLOCK": "records",
    "POLICY": "policy",
    "NIST-INSPIRED RISK GATE": "risk",
    "+1 LEGACY ADAPTER": "record",
    "AUDIT": "record",
}


def _infer_status(message: str, detail: str) -> str:
    text = f"{message} {detail}".upper()
    if "FAIL" in text or "DENIED" in text:
        return "fail"
    if "HUMAN" in text or "ESCALAT" in text or "SUSPICIOUS" in text:
        return "warn"
    if "PASS" in text or "COMPLETED" in text or "GRANTED" in text:
        return "pass"
    return "info"


class _Run:
    """State for one request: its trace, audit record and escalation helper."""

    def __init__(self, user_input: str, on_step: StepCallback | None) -> None:
        self.workflow_id = uuid.uuid4().hex[:12]
        self.user_input = user_input
        self.on_step = on_step
        self.steps: list[dict[str, Any]] = []
        self.plan: dict[str, Any] = {}
        self.national_id = ""
        self.confidence = 0.0
        self.audit: dict[str, Any] = {
            "workflow_id": self.workflow_id,
            "request": user_input,
            "interpreter": None,
            "intent": None,
            "national_id": None,
            "tools_planned": [],
            "consent_checks": [],
            "risk_checks": None,
            "final_status": "STARTED",
        }

    def step(self, label: str, message: str, detail: str = "", status: str | None = None) -> None:
        entry = {
            "label": label,
            "stage": STAGES.get(label, "interpret"),
            "message": message,
            "detail": detail,
            "status": status or _infer_status(message, detail),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.steps.append(entry)
        logger.debug("[%s] %s %s", label, message, detail)
        if self.on_step:
            self.on_step(entry)

    def escalate(
        self,
        flags: list[str],
        reason: str,
        final_status: str,
        consent_status: str = "N/A",
        **extra: Any,
    ) -> _Stop:
        """Record a human-review case and return the _Stop to raise."""
        escalation = create_escalation(
            case_id=f"ESC-{self.workflow_id}",
            national_id=self.national_id,
            user_request=self.user_input,
            workflow=self.plan.get("intent", ""),
            model_output=self.plan,
            confidence=self.confidence,
            risk_flags=flags,
            consent_status=consent_status,
            decision="ESCALATED",
            reason=reason,
        )
        result = {"success": False, "status": "ESCALATED", "escalation": escalation, **extra}
        return _Stop(result, final_status)

    def finish(self, result: dict[str, Any], final_status: str) -> dict[str, Any]:
        self.audit["final_status"] = final_status
        workflow_log.append(self.audit)
        result.setdefault("workflow_id", self.workflow_id)
        result["interpreter"] = self.audit["interpreter"]
        result["steps"] = self.steps
        return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def process_request(
    user_input: str,
    provider: LLMProvider | None = None,
    on_step: StepCallback | None = None,
) -> dict[str, Any]:
    """
    Run a citizen's natural-language request through the full DPI workflow.

    Args:
        user_input: the citizen's request
        provider: interprets the request; defaults to the deterministic rules.
            If a model provider fails, the rules are used instead and the
            fallback is recorded in the trace and audit log.
        on_step: called with each trace step as it happens (for live display)
    """
    run = _Run(user_input, on_step)
    try:
        result, final_status = _run_pipeline(run, provider or DeterministicProvider())
    except _Stop as stop:
        result, final_status = stop.result, stop.final_status
    except Exception as exc:  # noqa: BLE001 - report any failure as a result, never crash
        logger.exception("Workflow %s failed", run.workflow_id)
        run.audit["error"] = str(exc)
        result, final_status = {"success": False, "status": "ERROR", "error": str(exc)}, "ERROR"
    return run.finish(result, final_status)


Outcome = tuple[dict[str, Any], str]  # (result for the caller, final status for the audit log)


def _run_pipeline(run: _Run, provider: LLMProvider) -> Outcome:
    # 1. Identity claim: no model call until the citizen has stated an ID
    if (ask := check_identity_claim(run.user_input)) is not None:
        run.step("TRIAGE", "No national ID found in request", status="warn")
        raise _Stop(
            {"success": False, "status": "NEEDS_NATIONAL_ID", "message": ask.reason},
            "NEEDS_NATIONAL_ID",
        )

    # 2. Interpretation by the model (or rules)
    run.step("PUBLIC AGENT", "Understanding request...")
    try:
        interpretation = provider.interpret(run.user_input)
    except Exception as exc:  # noqa: BLE001 - any model failure falls back to rules
        run.step("PUBLIC AGENT", "Model unavailable, using deterministic rules", str(exc), "warn")
        interpretation = DeterministicProvider().interpret(run.user_input)
        run.audit["interpreter_fallback_reason"] = str(exc)

    plan = dict(interpretation.plan)
    run.plan = plan
    run.audit["interpreter"] = interpretation.interpreter
    run.audit["interpretation_ms"] = round(interpretation.latency_ms, 1)
    run.audit["intent"] = plan.get("intent")
    run.step(
        "PUBLIC AGENT",
        f"Intent: {plan.get('intent', 'UNKNOWN')}",
        f"Confidence: {plan.get('confidence', 'N/A')} | Interpreter: {interpretation.interpreter}",
    )

    # 3. Triage: schema, ID grounding, supported + corroborated intent
    decision = triage(run.user_input, plan)
    if decision.action == "invalid":
        run.step("TRIAGE", "Schema validation FAILED", "; ".join(decision.errors))
        raise _Stop(
            {
                "success": False,
                "status": "FAILED_VALIDATION",
                "errors": decision.errors,
                "message": "The request could not be processed due to validation errors.",
            },
            "FAILED_VALIDATION",
        )

    run.national_id = str(plan["national_id"]).strip()
    run.confidence = float(plan["confidence"])
    run.audit["national_id"] = run.national_id

    if decision.action == "escalate":
        run.step(
            "TRIAGE",
            f"Triage: {decision.reason}",
            "Escalating to human review",
            status="warn",
        )
        raise run.escalate(decision.flags, decision.reason, f"ESCALATED_{decision.flags[0]}")

    run.step("TRIAGE", "Triage PASS", "ID grounded in request; model and rules agree")

    # The workflow, not the model, decides which tools run
    plan.setdefault(
        "required_tools",
        [
            {"tool": tool, "arguments": {"national_id": run.national_id}}
            for tool in WORKFLOW_TOOL_PLANS[plan["intent"]]
        ],
    )
    run.audit["tools_planned"] = [t["tool"] for t in plan["required_tools"]]

    if plan["intent"] == SUBSIDY_WORKFLOW:
        return _subsidy_workflow(run)
    return _credit_workflow(run)


# ---------------------------------------------------------------------------
# Shared steps
# ---------------------------------------------------------------------------


def _verified_identity(run: _Run) -> dict[str, Any]:
    run.step("DPI WORKFLOW", "Identity verification...")
    result = verify_identity(run.national_id)
    if not result.get("success"):
        run.step("DPI WORKFLOW", f"Identity verification: FAIL - {result.get('error')}")
        raise run.escalate(
            ["IDENTITY_NOT_FOUND"],
            "Identity not found in registry",
            "ESCALATED_IDENTITY_NOT_FOUND",
        )

    identity = result["data"]
    status = identity["identity_status"]
    if status != "ACTIVE":
        run.step("DPI WORKFLOW", f"Identity verification: FAIL - Status is {status}")
        raise run.escalate(
            [f"IDENTITY_STATUS_{status}"],
            f"Identity status is {status}, must be ACTIVE",
            f"ESCALATED_IDENTITY_{status}",
            identity={"status": status, "name": identity.get("full_name")},
        )

    run.step(
        "DPI WORKFLOW",
        "Identity verification: PASS",
        f"Name: {identity['full_name']} | Status: {status}",
    )
    return identity


def _require_consent(run: _Run, kind: str, check: Callable, workflow: str) -> dict[str, Any]:
    """DEPA gate.  A denial is returned to the citizen, who controls their consent."""
    run.step("DEPA", f"Checking {kind} data consent...")
    consent = check(run.national_id, workflow)
    run.audit["consent_checks"].append(
        {"type": kind, "valid": consent["valid"], "reason": consent["reason"]}
    )
    if not consent["valid"]:
        run.step("DEPA", f"{kind.capitalize()} consent: DENIED - {consent['reason']}")
        raise _Stop(
            {
                "success": False,
                "status": "DENIED_CONSENT",
                "consent": consent,
                "message": (
                    f"Consent denied: {consent['reason']}. Your {kind} data cannot be "
                    "accessed until you grant consent for this purpose."
                ),
            },
            "DENIED_CONSENT",
        )
    run.step(
        "DEPA", f"{kind.capitalize()} consent: PASS", f"Consent ID: {consent.get('consent_id')}"
    )
    return consent


def _risk_gate(run: _Run) -> dict[str, Any]:
    run.step("NIST-INSPIRED RISK GATE", "Evaluating risk...")
    risk = apply_risk_gate(run.plan)
    threshold = risk.get("confidence_check", {}).get("threshold", CONFIDENCE_THRESHOLD)
    run.step(
        "NIST-INSPIRED RISK GATE",
        f"Confidence: {run.confidence:.2f}",
        f"Threshold: {threshold:.2f} (application-specific prototype policy, "
        "not prescribed by NIST AI RMF)",
    )
    run.audit["risk_checks"] = {"passed": risk["passed"], "flags": risk.get("all_flags", [])}
    return risk


def _write_back(run: _Run, service: str, consent: dict[str, Any]) -> dict[str, Any]:
    run.step("+1 LEGACY ADAPTER", "Creating transaction...")
    txn = create_legacy_transaction(
        national_id=run.national_id,
        service_requested=service,
        consent_token_used=consent.get("consent_id", "N/A"),
        slm_confidence_score=run.confidence,
        execution_status="COMPLETED",
    )
    if txn.get("success"):
        run.step("+1 LEGACY ADAPTER", f"Transaction created: {txn['data']['transaction_id']}")
    else:
        run.step("+1 LEGACY ADAPTER", f"Transaction write FAILED: {txn.get('error')}")
    return txn.get("data", {})


# ---------------------------------------------------------------------------
# Solar pump subsidy
# ---------------------------------------------------------------------------


def _subsidy_workflow(run: _Run) -> Outcome:
    identity = _verified_identity(run)
    consent = _require_consent(run, "agriculture", check_agriculture_consent, SUBSIDY_WORKFLOW)

    run.step("AI BLOCK", "Fetching land record...")
    land_result = fetch_land_record(run.national_id)
    if not land_result.get("success"):
        run.step("AI BLOCK", f"Land record: NOT FOUND - {land_result.get('error')}", status="fail")
        raise _Stop(
            {
                "success": False,
                "status": "FAILED_NO_LAND_RECORD",
                "error": land_result.get("error"),
            },
            "FAILED_NO_LAND_RECORD",
        )
    land = land_result["data"]
    run.step(
        "AI BLOCK",
        "Land record retrieved",
        f"Area: {land['land_area_acres']} acres | District: {land['district']} | "
        f"Crop: {land['current_crop']}",
    )

    run.step("POLICY", "Evaluating eligibility (prototype policy rule)...")
    land_area = float(land["land_area_acres"])
    eligibility_call = execute_tool(
        "evaluate_subsidy_eligibility",
        {
            "national_id": run.national_id,
            "land_area_acres": land_area,
            "identity_status": identity["identity_status"],
        },
    )
    eligibility = eligibility_call.get("result", {}).get("data", {})
    if not (eligibility_call.get("success") and eligibility.get("eligible")):
        run.step("POLICY", "Eligibility: FAIL", "; ".join(eligibility.get("reasons", ["Unknown"])))
        raise _Stop(
            {
                "success": False,
                "status": "DENIED_INELIGIBLE",
                "eligibility": eligibility,
                "message": "Eligibility criteria not met.",
            },
            "DENIED_INELIGIBLE",
        )
    run.step(
        "POLICY",
        "Eligibility: PASS",
        f"Land area {land_area} acres >= {MINIMUM_LAND_AREA_ACRES} acres (prototype policy rule)",
    )

    risk = _risk_gate(run)
    if not risk["passed"]:
        run.step("NIST-INSPIRED RISK GATE", "Decision: FAIL - escalating to human review")
        flags = risk.get("all_flags", [])
        raise run.escalate(
            flags,
            "Risk gate failed: " + ", ".join(flags),
            "ESCALATED_RISK_GATE",
            consent_status="VALID",
            risk_assessment=risk,
        )
    run.step("NIST-INSPIRED RISK GATE", "Decision: PASS")

    transaction = _write_back(run, "Solar Pump Climate Subsidy Application", consent)
    run.step("AUDIT", "Workflow completed successfully")
    txn_id = transaction.get("transaction_id", "N/A")
    return (
        {
            "success": True,
            "status": "COMPLETED",
            "citizen": {
                "name": identity["full_name"],
                "national_id": run.national_id,
                "preferred_language": identity.get("preferred_language", "en"),
            },
            "land": land,
            "eligibility": eligibility,
            "consent": consent,
            "risk_assessment": risk,
            "transaction": transaction,
            "message": (
                f"Solar pump subsidy application for {identity['full_name']} has been "
                f"approved and recorded. Transaction ID: {txn_id}."
            ),
        },
        "COMPLETED",
    )


# ---------------------------------------------------------------------------
# Agricultural credit (prototype decision support only)
# ---------------------------------------------------------------------------


def _credit_workflow(run: _Run) -> Outcome:
    """
    DISCLAIMER: this workflow never makes a legally binding credit decision.
    It is prototype decision support, and high-risk cases go to a human.
    """
    identity = _verified_identity(run)
    consent = _require_consent(run, "credit", check_credit_consent, CREDIT_WORKFLOW)

    run.step("AI BLOCK", "Fetching credit profile...")
    credit_result = fetch_credit_profile(run.national_id)
    if not credit_result.get("success"):
        run.step(
            "AI BLOCK", f"Credit profile: NOT FOUND - {credit_result.get('error')}", status="fail"
        )
        raise _Stop(
            {
                "success": False,
                "status": "FAILED_NO_CREDIT_RECORD",
                "error": credit_result.get("error"),
            },
            "FAILED_NO_CREDIT_RECORD",
        )
    credit = credit_result["data"]
    run.step(
        "AI BLOCK",
        "Credit profile retrieved",
        f"Score: {credit['credit_score']} | Repayment: {credit['repayment_history_status']}",
    )

    run.step("POLICY", "Evaluating cash-flow indicators (prototype decision support)...")
    score = credit.get("credit_score", 0)
    repayment = credit.get("repayment_history_status", "Unknown")
    score_pass = score >= MINIMUM_CREDIT_SCORE
    repayment_pass = repayment in ("Good", "Excellent")
    risk_tier = "LOW" if score_pass and repayment_pass else "MEDIUM" if score_pass else "HIGH"
    run.step(
        "POLICY",
        f"Credit Score: {'PASS' if score_pass else 'FAIL'} ({score} vs min {MINIMUM_CREDIT_SCORE})",
        f"Repayment History: {repayment} | GST: {credit.get('active_gst_billing_status')} | "
        f"Risk Tier: {risk_tier}",
    )

    risk = _risk_gate(run)
    run.step(
        "POLICY",
        "PROTOTYPE DECISION SUPPORT ONLY",
        "This is NOT a legally binding credit decision.",
    )

    summary = {
        "credit_score": score,
        "risk_tier": risk_tier,
        "repayment_history": repayment,
        "note": "Prototype decision support - not a legally binding credit decision",
    }
    if not risk["passed"] or risk_tier == "HIGH":
        run.step("NIST-INSPIRED RISK GATE", "Decision: REQUIRES HUMAN REVIEW")
        flags = risk.get("all_flags", []) + (["HIGH_RISK_TIER"] if risk_tier == "HIGH" else [])
        raise run.escalate(
            flags,
            "Credit assessment requires human review",
            "ESCALATED_HUMAN_REVIEW",
            consent_status="VALID",
            credit_summary=summary,
        )

    transaction = _write_back(run, "Agricultural Credit Assessment", consent)
    run.step("AUDIT", "Workflow completed")
    return (
        {
            "success": True,
            "status": "COMPLETED",
            "citizen": {"name": identity["full_name"], "national_id": run.national_id},
            "credit_summary": {
                **summary,
                "avg_monthly_turnover_inr": credit.get("avg_monthly_turnover_inr"),
                "gst_status": credit.get("active_gst_billing_status"),
                "minimum_credit_score": MINIMUM_CREDIT_SCORE,
            },
            "consent": consent,
            "risk_assessment": risk,
            "transaction": transaction,
            "message": (
                f"Agricultural credit assessment for {identity['full_name']} completed. "
                f"Risk tier: {risk_tier}. This is prototype decision support only, "
                "not a binding credit decision."
            ),
        },
        "COMPLETED",
    )
