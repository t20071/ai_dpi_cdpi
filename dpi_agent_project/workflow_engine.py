"""
Sovereign DPI-AI Public Agent — Workflow Engine

The main DPI Workflow orchestration layer.  Every user request follows a
deterministic pipeline that the SLM cannot bypass:

    USER REQUEST → SLM INTERPRETATION → STRUCTURED PLAN → SCHEMA VALIDATION →
    IDENTITY RESOLUTION → CONSENT CHECK → TOOL AUTHORIZATION → AI BLOCK EXECUTION →
    POLICY / ELIGIBILITY CHECK → NIST-INSPIRED RISK GATE → HUMAN ESCALATION OR APPROVAL →
    +1 LEGACY WRITE-BACK → AUDIT → USER RESPONSE

The model proposes.  The workflow decides.  The DPI tools execute.
The policy engine authorizes.  The audit layer records.
The human handles uncertain / high-impact cases.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from config import (
    CONFIDENCE_THRESHOLD,
    MINIMUM_LAND_AREA_ACRES,
    MINIMUM_CREDIT_SCORE,
    SUPPORTED_WORKFLOWS,
    WORKFLOW_AUDIT_LOG,
)
from dpi_registries import verify_identity, fetch_land_record, fetch_credit_profile
from depa_consent import (
    check_agriculture_consent,
    check_credit_consent,
    validate_consent,
)
from mcp_tools import execute_tool
from nist_safeguards import (
    apply_risk_gate,
    create_escalation,
    evaluate_confidence,
)
from legacy_adapter import create_legacy_transaction
from model_client import generate_json, get_default_provider, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Workflow audit logging
# ---------------------------------------------------------------------------

def _write_workflow_audit(record: Dict[str, Any]) -> None:
    """Append a structured audit record to the workflow audit log."""
    WORKFLOW_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with open(WORKFLOW_AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ---------------------------------------------------------------------------
# Schema validation for SLM output
# ---------------------------------------------------------------------------

def _validate_slm_output(output: Dict[str, Any]) -> List[str]:
    """
    Validate the structured SLM output against the expected schema.

    Returns a list of validation errors (empty if valid).
    """
    errors: List[str] = []

    if not isinstance(output, dict):
        return ["SLM output is not a JSON object"]

    # Required fields
    for field in ["intent", "national_id", "requested_action"]:
        if not output.get(field):
            errors.append(f"Missing or empty required field: '{field}'")

    # Confidence must be a number
    confidence = output.get("confidence")
    if confidence is None:
        errors.append("Missing 'confidence' field")
    elif not isinstance(confidence, (int, float)):
        errors.append(f"'confidence' must be a number, got {type(confidence).__name__}")
    elif not (0.0 <= confidence <= 1.0):
        errors.append(f"'confidence' must be between 0.0 and 1.0, got {confidence}")

    # required_tools validation
    tools = output.get("required_tools", [])
    if not isinstance(tools, list):
        errors.append("'required_tools' must be a list")
    else:
        for i, tool_spec in enumerate(tools):
            if not isinstance(tool_spec, dict):
                errors.append(f"required_tools[{i}] is not a dict")
                continue
            if "tool" not in tool_spec:
                errors.append(f"required_tools[{i}] missing 'tool' name")
            if "arguments" not in tool_spec:
                errors.append(f"required_tools[{i}] missing 'arguments'")

    return errors


import threading
_step_context = threading.local()

# ---------------------------------------------------------------------------
# Workflow step display
# ---------------------------------------------------------------------------

def _step(label: str, message: str, detail: str = "") -> None:
    """Print a visible workflow step to the terminal and record in step context."""
    print(f"\n  [{label}]")
    print(f"  {message}")
    if detail:
        print(f"  {detail}")
    
    if hasattr(_step_context, "steps") and _step_context.steps is not None:
        status = "info"
        msg_upper = message.upper()
        det_upper = detail.upper()
        if "FAIL" in msg_upper or "DENIED" in msg_upper or "FAIL" in det_upper or "DENIED" in det_upper:
            status = "fail"
        elif "HUMAN" in msg_upper or "ESCALAT" in msg_upper or "SUSPICIOUS" in msg_upper:
            status = "warn"
        elif "PASS" in msg_upper or "COMPLETED" in msg_upper or "GRANTED" in msg_upper:
            status = "pass"
        
        _step_context.steps.append({
            "label": label,
            "message": message,
            "detail": detail,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


# ---------------------------------------------------------------------------
# Main request processor
# ---------------------------------------------------------------------------

def process_request(
    user_input: str,
    use_live_slm: bool = False,
    provider=None,
) -> Dict[str, Any]:
    """
    Process a citizen's natural-language request through the full DPI workflow.

    Args:
        user_input: The citizen's request in natural language
        use_live_slm: If True, use the configured LLM provider
        provider: Optional LLM provider override

    Returns:
        Structured workflow result
    """
    _step_context.steps = []
    workflow_id = str(uuid.uuid4())[:12]
    timestamp = datetime.now(timezone.utc).isoformat()

    audit_record = {
        "workflow_id": workflow_id,
        "timestamp": timestamp,
        "request": user_input,
        "intent": None,
        "tools_requested": [],
        "consent_checks": [],
        "tool_results_summary": [],
        "risk_checks": None,
        "final_status": "STARTED",
    }

    try:
        # ---------------------------------------------------------------
        # Step 1: SLM Interpretation
        # ---------------------------------------------------------------
        _step("PUBLIC AGENT", "Understanding request...")

        if use_live_slm:
            try:
                llm = provider or get_default_provider()
                slm_output = llm.generate_json(user_input)
            except Exception as e:
                _step("PUBLIC AGENT", f"SLM communication failed: {e}")
                _step("PUBLIC AGENT", "Falling back to deterministic interpretation...")
                from model_client import DeterministicProvider
                slm_output = DeterministicProvider().generate_json(user_input)
        else:
            from model_client import DeterministicProvider
            slm_output = DeterministicProvider().generate_json(user_input)

        _step("PUBLIC AGENT",
              f"Intent: {slm_output.get('intent', 'UNKNOWN')}",
              f"Confidence: {slm_output.get('confidence', 'N/A')}")

        audit_record["intent"] = slm_output.get("intent")
        audit_record["tools_requested"] = [
            t.get("tool", "") for t in slm_output.get("required_tools", [])
        ]

        # ---------------------------------------------------------------
        # Step 2: Schema Validation
        # ---------------------------------------------------------------
        validation_errors = _validate_slm_output(slm_output)
        if validation_errors:
            _step("VALIDATION", "Schema validation FAILED")
            for err in validation_errors:
                print(f"    X {err}")
            audit_record["final_status"] = "FAILED_VALIDATION"
            _write_workflow_audit(audit_record)
            return {
                "success": False,
                "workflow_id": workflow_id,
                "status": "FAILED_VALIDATION",
                "errors": validation_errors,
                "message": "The request could not be processed due to validation errors.",
            }

        # ---------------------------------------------------------------
        # Step 3: Route to workflow
        # ---------------------------------------------------------------
        intent = slm_output.get("intent", "")
        national_id = slm_output.get("national_id", "")
        confidence = float(slm_output.get("confidence", 0))

        if intent == "apply_for_solar_pump_subsidy":
            result = _execute_subsidy_workflow(
                workflow_id, national_id, confidence, slm_output, user_input, audit_record
            )
        elif intent == "assess_agricultural_credit":
            result = _execute_credit_workflow(
                workflow_id, national_id, confidence, slm_output, user_input, audit_record
            )
        else:
            _step("DPI WORKFLOW", f"Unsupported workflow intent: {intent}")
            # Escalate unsupported intents
            escalation = create_escalation(
                case_id=f"ESC-{workflow_id}",
                national_id=national_id,
                user_request=user_input,
                workflow=intent,
                model_output=slm_output,
                confidence=confidence,
                risk_flags=["UNSUPPORTED_WORKFLOW"],
                consent_status="N/A",
                decision="ESCALATED",
                reason="Workflow intent not supported",
            )
            audit_record["final_status"] = "ESCALATED"
            _write_workflow_audit(audit_record)
            return {
                "success": False,
                "workflow_id": workflow_id,
                "status": "ESCALATED",
                "escalation": escalation,
            }

        if isinstance(result, dict) and "steps" not in result:
            result["steps"] = list(_step_context.steps)
        _write_workflow_audit(audit_record)
        return result

    except Exception as e:
        logger.error("Workflow error: %s", str(e), exc_info=True)
        audit_record["final_status"] = "ERROR"
        audit_record["error"] = str(e)
        _write_workflow_audit(audit_record)
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "ERROR",
            "error": str(e),
            "steps": list(getattr(_step_context, "steps", []) or []),
        }


# ---------------------------------------------------------------------------
# Subsidy Workflow: apply_for_solar_pump_subsidy
# ---------------------------------------------------------------------------

def _execute_subsidy_workflow(
    workflow_id: str,
    national_id: str,
    confidence: float,
    slm_output: Dict[str, Any],
    user_request: str,
    audit_record: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute the solar pump subsidy workflow.

    Steps:
    1. Verify identity → confirm ACTIVE
    2. Verify consent for agriculture data
    3. Fetch land record
    4. Evaluate deterministic eligibility
    5. Evaluate SLM confidence
    6. Apply risk gate
    7. If approved → create legacy transaction
    8. Audit entire workflow
    """
    workflow_name = "apply_for_solar_pump_subsidy"

    # --- Step 1: Identity Verification ---
    _step("DPI WORKFLOW", "Identity verification...")
    identity_result = verify_identity(national_id)

    if not identity_result.get("success"):
        _step("DPI WORKFLOW", f"Identity verification: FAIL — {identity_result.get('error')}")
        # Escalate
        escalation = create_escalation(
            case_id=f"ESC-{workflow_id}",
            national_id=national_id,
            user_request=user_request,
            workflow=workflow_name,
            model_output=slm_output,
            confidence=confidence,
            risk_flags=["IDENTITY_NOT_FOUND"],
            consent_status="N/A",
            decision="ESCALATED",
            reason="Identity not found in registry",
        )
        audit_record["final_status"] = "ESCALATED_IDENTITY_NOT_FOUND"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "ESCALATED",
            "escalation": escalation,
        }

    identity_data = identity_result["data"]
    identity_status = identity_data["identity_status"]

    if identity_status != "ACTIVE":
        _step("DPI WORKFLOW",
              f"Identity verification: FAIL — Status is {identity_status}")
        escalation = create_escalation(
            case_id=f"ESC-{workflow_id}",
            national_id=national_id,
            user_request=user_request,
            workflow=workflow_name,
            model_output=slm_output,
            confidence=confidence,
            risk_flags=[f"IDENTITY_STATUS_{identity_status}"],
            consent_status="N/A",
            decision="ESCALATED",
            reason=f"Identity status is {identity_status}, must be ACTIVE",
        )
        audit_record["final_status"] = f"ESCALATED_IDENTITY_{identity_status}"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "ESCALATED",
            "escalation": escalation,
            "identity": {"status": identity_status, "name": identity_data.get("full_name")},
        }

    _step("DPI WORKFLOW",
          f"Identity verification: PASS",
          f"Name: {identity_data['full_name']} | Status: {identity_status}")

    # --- Step 2: DEPA Consent Check ---
    _step("DEPA", "Checking agriculture data consent...")
    consent_result = check_agriculture_consent(national_id, workflow_name)
    audit_record["consent_checks"].append({
        "type": "agriculture",
        "valid": consent_result["valid"],
        "reason": consent_result["reason"],
    })

    if not consent_result["valid"]:
        _step("DEPA", f"Agriculture consent: DENIED — {consent_result['reason']}")
        escalation = create_escalation(
            case_id=f"ESC-{workflow_id}",
            national_id=national_id,
            user_request=user_request,
            workflow=workflow_name,
            model_output=slm_output,
            confidence=confidence,
            risk_flags=["CONSENT_DENIED"],
            consent_status=consent_result["reason"],
            decision="ESCALATED",
            reason=f"Consent denied: {consent_result['reason']}",
        )
        audit_record["final_status"] = "DENIED_CONSENT"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "DENIED_CONSENT",
            "consent": consent_result,
            "message": f"Consent denied: {consent_result['reason']}. Agriculture data cannot be accessed.",
        }

    _step("DEPA",
          f"Agriculture consent: PASS",
          f"Consent ID: {consent_result.get('consent_id', 'N/A')}")

    # --- Step 3: Fetch Land Record ---
    _step("AI BLOCK", "Fetching land record...")
    land_result = fetch_land_record(national_id)

    if not land_result.get("success"):
        _step("AI BLOCK", f"Land record: NOT FOUND — {land_result.get('error')}")
        audit_record["final_status"] = "FAILED_NO_LAND_RECORD"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "FAILED_NO_LAND_RECORD",
            "error": land_result.get("error"),
        }

    land_data = land_result["data"]
    _step("AI BLOCK",
          "Land record retrieved",
          f"Area: {land_data['land_area_acres']} acres | "
          f"District: {land_data['district']} | "
          f"Crop: {land_data['current_crop']}")

    # --- Step 4: Deterministic Eligibility Check ---
    _step("POLICY", "Evaluating eligibility (prototype policy rule)...")
    land_area = float(land_data["land_area_acres"])

    eligibility_result = execute_tool("evaluate_subsidy_eligibility", {
        "national_id": national_id,
        "land_area_acres": land_area,
        "identity_status": identity_status,
    })

    eligible = (
        eligibility_result.get("success")
        and eligibility_result.get("result", {}).get("data", {}).get("eligible", False)
    )

    if not eligible:
        reasons = eligibility_result.get("result", {}).get("data", {}).get("reasons", ["Unknown"])
        _step("POLICY", f"Eligibility: FAIL")
        for r in reasons:
            print(f"    X {r}")
        audit_record["final_status"] = "DENIED_INELIGIBLE"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "DENIED_INELIGIBLE",
            "eligibility": eligibility_result.get("result", {}).get("data", {}),
            "message": "Eligibility criteria not met.",
        }

    _step("POLICY",
          "Eligibility: PASS",
          f"Land area {land_area} acres >= {MINIMUM_LAND_AREA_ACRES} acres (prototype policy rule)")

    # --- Step 5 & 6: NIST-Inspired Risk Gate ---
    _step("NIST-INSPIRED RISK GATE", "Evaluating risk...")
    risk_result = apply_risk_gate(slm_output)

    confidence_check = risk_result.get("confidence_check", {})
    _step("NIST-INSPIRED RISK GATE",
          f"Confidence: {confidence:.2f}",
          f"Threshold: {confidence_check.get('threshold', CONFIDENCE_THRESHOLD):.2f} "
          f"(application-specific prototype policy — NOT prescribed by NIST AI RMF)")

    audit_record["risk_checks"] = {
        "passed": risk_result["passed"],
        "flags": risk_result.get("all_flags", []),
    }

    if not risk_result["passed"]:
        _step("NIST-INSPIRED RISK GATE", "Decision: FAIL — escalating to human review")
        escalation = create_escalation(
            case_id=f"ESC-{workflow_id}",
            national_id=national_id,
            user_request=user_request,
            workflow=workflow_name,
            model_output=slm_output,
            confidence=confidence,
            risk_flags=risk_result.get("all_flags", []),
            consent_status="VALID",
            decision="ESCALATED",
            reason="Risk gate failed: " + ", ".join(risk_result.get("all_flags", [])),
        )
        audit_record["final_status"] = "ESCALATED_RISK_GATE"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "ESCALATED",
            "risk_assessment": risk_result,
            "escalation": escalation,
        }

    _step("NIST-INSPIRED RISK GATE", "Decision: PASS")

    # --- Step 7: +1 Legacy Write-back ---
    _step("+1 LEGACY ADAPTER", "Creating transaction...")
    txn_result = create_legacy_transaction(
        national_id=national_id,
        service_requested="Solar Pump Climate Subsidy Application",
        consent_token_used=consent_result.get("consent_id", "N/A"),
        slm_confidence_score=confidence,
        execution_status="COMPLETED",
    )

    if txn_result.get("success"):
        txn_id = txn_result["data"]["transaction_id"]
        _step("+1 LEGACY ADAPTER",
              f"Transaction created: {txn_id}")
    else:
        _step("+1 LEGACY ADAPTER",
              f"Transaction write failed: {txn_result.get('error')}")

    # --- Step 8: Audit ---
    _step("AUDIT", "Workflow completed successfully")
    audit_record["final_status"] = "COMPLETED"
    audit_record["tool_results_summary"].append({
        "identity": "VERIFIED",
        "consent": "VALID",
        "land_record": "RETRIEVED",
        "eligibility": "PASS",
        "risk_gate": "PASS",
        "transaction": txn_result.get("data", {}).get("transaction_id", "N/A"),
    })

    return {
        "success": True,
        "workflow_id": workflow_id,
        "status": "COMPLETED",
        "citizen": {
            "name": identity_data["full_name"],
            "national_id": national_id,
            "preferred_language": identity_data.get("preferred_language", "en"),
        },
        "land": land_data,
        "eligibility": eligibility_result.get("result", {}).get("data", {}),
        "consent": consent_result,
        "risk_assessment": risk_result,
        "transaction": txn_result.get("data", {}),
        "message": (
            f"Solar pump subsidy application for {identity_data['full_name']} "
            f"has been approved and recorded. "
            f"Transaction ID: {txn_result.get('data', {}).get('transaction_id', 'N/A')}."
        ),
    }


# ---------------------------------------------------------------------------
# Credit Workflow: assess_agricultural_credit
# ---------------------------------------------------------------------------

def _execute_credit_workflow(
    workflow_id: str,
    national_id: str,
    confidence: float,
    slm_output: Dict[str, Any],
    user_request: str,
    audit_record: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute the agricultural credit assessment workflow.

    DISCLAIMER: This workflow MUST NOT make a legally binding or real credit
    decision.  It is presented as PROTOTYPE DECISION SUPPORT only.

    Steps:
    1. Verify identity
    2. Check consent for credit data
    3. Fetch credit registry
    4. Evaluate cash-flow indicators
    5. Apply policy calculation
    6. Risk gate
    7. Human review if needed
    """
    workflow_name = "assess_agricultural_credit"

    # --- Step 1: Identity Verification ---
    _step("DPI WORKFLOW", "Identity verification...")
    identity_result = verify_identity(national_id)

    if not identity_result.get("success"):
        _step("DPI WORKFLOW", f"Identity verification: FAIL — {identity_result.get('error')}")
        escalation = create_escalation(
            case_id=f"ESC-{workflow_id}",
            national_id=national_id,
            user_request=user_request,
            workflow=workflow_name,
            model_output=slm_output,
            confidence=confidence,
            risk_flags=["IDENTITY_NOT_FOUND"],
            consent_status="N/A",
            decision="ESCALATED",
            reason="Identity not found in registry",
        )
        audit_record["final_status"] = "ESCALATED_IDENTITY_NOT_FOUND"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "ESCALATED",
            "escalation": escalation,
        }

    identity_data = identity_result["data"]
    identity_status = identity_data["identity_status"]

    if identity_status != "ACTIVE":
        _step("DPI WORKFLOW", f"Identity verification: FAIL — Status is {identity_status}")
        escalation = create_escalation(
            case_id=f"ESC-{workflow_id}",
            national_id=national_id,
            user_request=user_request,
            workflow=workflow_name,
            model_output=slm_output,
            confidence=confidence,
            risk_flags=[f"IDENTITY_STATUS_{identity_status}"],
            consent_status="N/A",
            decision="ESCALATED",
            reason=f"Identity status is {identity_status}, must be ACTIVE",
        )
        audit_record["final_status"] = f"ESCALATED_IDENTITY_{identity_status}"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "ESCALATED",
            "escalation": escalation,
        }

    _step("DPI WORKFLOW",
          f"Identity verification: PASS",
          f"Name: {identity_data['full_name']} | Status: {identity_status}")

    # --- Step 2: DEPA Consent Check for Credit Data ---
    _step("DEPA", "Checking credit data consent...")
    consent_result = check_credit_consent(national_id, workflow_name)
    audit_record["consent_checks"].append({
        "type": "credit",
        "valid": consent_result["valid"],
        "reason": consent_result["reason"],
    })

    if not consent_result["valid"]:
        _step("DEPA", f"Credit consent: DENIED — {consent_result['reason']}")
        audit_record["final_status"] = "DENIED_CONSENT"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "DENIED_CONSENT",
            "consent": consent_result,
            "message": (
                f"Consent denied: {consent_result['reason']}. "
                "Credit data cannot be accessed without appropriate consent."
            ),
        }

    _step("DEPA",
          f"Credit consent: PASS",
          f"Consent ID: {consent_result.get('consent_id', 'N/A')}")

    # --- Step 3: Fetch Credit Profile ---
    _step("AI BLOCK", "Fetching credit profile...")
    credit_result = fetch_credit_profile(national_id)

    if not credit_result.get("success"):
        _step("AI BLOCK", f"Credit profile: NOT FOUND — {credit_result.get('error')}")
        audit_record["final_status"] = "FAILED_NO_CREDIT_RECORD"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "FAILED_NO_CREDIT_RECORD",
            "error": credit_result.get("error"),
        }

    credit_data = credit_result["data"]
    _step("AI BLOCK",
          "Credit profile retrieved",
          f"Score: {credit_data['credit_score']} | "
          f"Repayment: {credit_data['repayment_history_status']}")

    # --- Step 4: Cash-flow Indicators ---
    _step("POLICY", "Evaluating cash-flow indicators (prototype decision support)...")
    turnover = credit_data.get("avg_monthly_turnover_inr", 0)
    credit_score = credit_data.get("credit_score", 0)
    repayment = credit_data.get("repayment_history_status", "Unknown")
    gst_status = credit_data.get("active_gst_billing_status", "Unknown")

    # Deterministic policy calculation (NOT a real credit decision)
    score_pass = credit_score >= MINIMUM_CREDIT_SCORE
    repayment_pass = repayment in ("Good", "Excellent")
    risk_tier = "LOW" if (score_pass and repayment_pass) else "MEDIUM" if score_pass else "HIGH"

    _step("POLICY",
          f"Credit Score: {'PASS' if score_pass else 'FAIL'} "
          f"({credit_score} vs min {MINIMUM_CREDIT_SCORE})",
          f"Repayment History: {repayment} | GST: {gst_status} | Risk Tier: {risk_tier}")

    # --- Step 5: Risk Gate ---
    _step("NIST-INSPIRED RISK GATE", "Evaluating risk...")
    risk_result = apply_risk_gate(slm_output)
    confidence_check = risk_result.get("confidence_check", {})

    _step("NIST-INSPIRED RISK GATE",
          f"Confidence: {confidence:.2f}",
          f"Threshold: {confidence_check.get('threshold', CONFIDENCE_THRESHOLD):.2f}")

    audit_record["risk_checks"] = {
        "passed": risk_result["passed"],
        "flags": risk_result.get("all_flags", []),
    }

    # Credit assessments ALWAYS get human review note
    _step("POLICY",
          "PROTOTYPE DECISION SUPPORT ONLY",
          "This is NOT a legally binding credit decision.")

    if not risk_result["passed"] or risk_tier == "HIGH":
        _step("NIST-INSPIRED RISK GATE", "Decision: REQUIRES HUMAN REVIEW")
        escalation = create_escalation(
            case_id=f"ESC-{workflow_id}",
            national_id=national_id,
            user_request=user_request,
            workflow=workflow_name,
            model_output=slm_output,
            confidence=confidence,
            risk_flags=risk_result.get("all_flags", []) + ([f"HIGH_RISK_TIER"] if risk_tier == "HIGH" else []),
            consent_status="VALID",
            decision="ESCALATED",
            reason="Credit assessment requires human review",
        )
        audit_record["final_status"] = "ESCALATED_HUMAN_REVIEW"
        return {
            "success": False,
            "workflow_id": workflow_id,
            "status": "ESCALATED",
            "escalation": escalation,
            "credit_summary": {
                "credit_score": credit_score,
                "risk_tier": risk_tier,
                "repayment_history": repayment,
                "note": "Prototype decision support — not a binding credit decision",
            },
        }

    # --- Step 6: Legacy Write-back ---
    _step("+1 LEGACY ADAPTER", "Creating transaction...")
    txn_result = create_legacy_transaction(
        national_id=national_id,
        service_requested="Agricultural Credit Assessment",
        consent_token_used=consent_result.get("consent_id", "N/A"),
        slm_confidence_score=confidence,
        execution_status="COMPLETED",
    )

    if txn_result.get("success"):
        _step("+1 LEGACY ADAPTER", f"Transaction created: {txn_result['data']['transaction_id']}")
    else:
        _step("+1 LEGACY ADAPTER", f"Transaction write failed: {txn_result.get('error')}")

    # --- Step 7: Audit ---
    _step("AUDIT", "Workflow completed")
    audit_record["final_status"] = "COMPLETED"

    return {
        "success": True,
        "workflow_id": workflow_id,
        "status": "COMPLETED",
        "citizen": {
            "name": identity_data["full_name"],
            "national_id": national_id,
        },
        "credit_summary": {
            "credit_score": credit_score,
            "avg_monthly_turnover_inr": turnover,
            "repayment_history": repayment,
            "gst_status": gst_status,
            "risk_tier": risk_tier,
            "minimum_credit_score": MINIMUM_CREDIT_SCORE,
            "note": "Prototype decision support — not a legally binding credit decision",
        },
        "consent": consent_result,
        "risk_assessment": risk_result,
        "transaction": txn_result.get("data", {}),
        "message": (
            f"Agricultural credit assessment for {identity_data['full_name']} completed. "
            f"Risk tier: {risk_tier}. "
            "This is prototype decision support only — not a binding credit decision."
        ),
    }


# ---------------------------------------------------------------------------
# Scenario runners
# ---------------------------------------------------------------------------

def run_scenario_a() -> Dict[str, Any]:
    """
    Scenario A — Subsidy Success

    Citizen Suresh Kaur (765-741-434) applies for a solar pump subsidy.
    Expected: full success pipeline.
    """
    from config import SCENARIO_CITIZENS
    citizen = SCENARIO_CITIZENS["scenario_a"]

    print("=" * 60)
    print(f"  SCENARIO A: Subsidy Success")
    print(f"  Citizen: {citizen['name']} (ID: {citizen['national_id']})")
    print(f"  {citizen['description']}")
    print("=" * 60)

    request = (
        f"I am {citizen['name']}, ID {citizen['national_id']}. "
        "I want to apply for a solar pump subsidy."
    )
    print(f"\n  Citizen > {request}")

    return process_request(request)


def run_scenario_b() -> Dict[str, Any]:
    """
    Scenario B — DEPA Consent Failure

    Citizen Anjali Iyer (213-163-577) requests credit information but
    consent record only permits agriculture data, not credit.
    Expected: consent denied for credit data.
    """
    from config import SCENARIO_CITIZENS
    citizen = SCENARIO_CITIZENS["scenario_b"]

    print("=" * 60)
    print(f"  SCENARIO B: DEPA Consent Failure")
    print(f"  Citizen: {citizen['name']} (ID: {citizen['national_id']})")
    print(f"  {citizen['description']}")
    print("=" * 60)

    request = (
        f"I am {citizen['name']}, ID {citizen['national_id']}. "
        "I want to assess my credit for an agricultural loan."
    )
    print(f"\n  Citizen > {request}")

    return process_request(request)


def run_scenario_c() -> Dict[str, Any]:
    """
    Scenario C — Human Escalation

    Citizen Suresh Mishra (715-454-455) has a SUSPENDED identity.
    Expected: identity check fails → automatic escalation.
    """
    from config import SCENARIO_CITIZENS
    citizen = SCENARIO_CITIZENS["scenario_c"]

    print("=" * 60)
    print(f"  SCENARIO C: Human Escalation")
    print(f"  Citizen: {citizen['name']} (ID: {citizen['national_id']})")
    print(f"  {citizen['description']}")
    print("=" * 60)

    request = (
        f"I am {citizen['name']}, ID {citizen['national_id']}. "
        "I need help, I'm not sure how to apply for a solar pump subsidy."
    )
    print(f"\n  Citizen > {request}")

    return process_request(request)
