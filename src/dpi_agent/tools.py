"""
Sovereign DPI-AI Public Agent — MCP-Style Tool Registry

Local MCP-style tool registry and execution router.

DISCLAIMER: This is a LOCAL prototype tool registry inspired by the MCP
(Model Context Protocol) pattern. It is NOT a complete MCP server
implementation and does not implement the full MCP protocol specification.

The registry:
    - Defines JSON schemas for each AI Block / callable service
    - Validates tool names against an allowlist
    - Validates arguments against schemas
    - Routes execution to the correct handler
    - Returns structured JSON responses
    - NEVER executes arbitrary code
    - Rejects unknown tools and parameters
"""

import logging
from collections.abc import Callable
from typing import Any

from dpi_agent.config import ALLOWED_TOOLS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schema definitions
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "verify_identity": {
        "name": "verify_identity",
        "description": "Verify a citizen identity record from the MOSIP registry",
        "input_schema": {
            "type": "object",
            "properties": {
                "national_id": {
                    "type": "string",
                    "description": "The citizen's national identifier",
                },
            },
            "required": ["national_id"],
        },
    },
    "fetch_land_record": {
        "name": "fetch_land_record",
        "description": "Fetch agriculture land record for a citizen",
        "input_schema": {
            "type": "object",
            "properties": {
                "national_id": {
                    "type": "string",
                    "description": "The citizen's national identifier",
                },
            },
            "required": ["national_id"],
        },
    },
    "assess_credit_profile": {
        "name": "assess_credit_profile",
        "description": "Fetch credit profile from Mifos credit registry",
        "input_schema": {
            "type": "object",
            "properties": {
                "national_id": {
                    "type": "string",
                    "description": "The citizen's national identifier",
                },
            },
            "required": ["national_id"],
        },
    },
    "check_consent": {
        "name": "check_consent",
        "description": "Check DEPA consent for data access",
        "input_schema": {
            "type": "object",
            "properties": {
                "national_id": {
                    "type": "string",
                    "description": "The citizen's national identifier",
                },
                "required_permissions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Data categories requiring consent (e.g., agri_land, mifos_credit)",
                },
                "workflow_purpose": {
                    "type": "string",
                    "description": "The workflow intent name",
                },
            },
            "required": ["national_id", "required_permissions", "workflow_purpose"],
        },
    },
    "evaluate_subsidy_eligibility": {
        "name": "evaluate_subsidy_eligibility",
        "description": "Evaluate deterministic eligibility criteria for subsidy programs",
        "input_schema": {
            "type": "object",
            "properties": {
                "national_id": {
                    "type": "string",
                    "description": "The citizen's national identifier",
                },
                "land_area_acres": {
                    "type": "number",
                    "description": "The citizen's land area in acres",
                },
                "identity_status": {
                    "type": "string",
                    "description": "Current identity verification status",
                },
            },
            "required": ["national_id", "land_area_acres", "identity_status"],
        },
    },
    "create_legacy_transaction": {
        "name": "create_legacy_transaction",
        "description": "Create a transaction in the +1 legacy municipal system",
        "input_schema": {
            "type": "object",
            "properties": {
                "national_id": {
                    "type": "string",
                    "description": "The citizen's national identifier",
                },
                "service_requested": {
                    "type": "string",
                    "description": "The service being requested",
                },
                "consent_token_used": {
                    "type": "string",
                    "description": "The consent token/ID used for this transaction",
                },
                "slm_confidence_score": {
                    "type": "number",
                    "description": "The SLM confidence score",
                },
                "execution_status": {
                    "type": "string",
                    "description": "The execution status (COMPLETED, ESCALATED_TO_HUMAN, etc.)",
                },
            },
            "required": [
                "national_id",
                "service_requested",
                "consent_token_used",
                "slm_confidence_score",
                "execution_status",
            ],
        },
    },
    "risk_check": {
        "name": "risk_check",
        "description": "Apply NIST-inspired risk gate assessment",
        "input_schema": {
            "type": "object",
            "properties": {
                "model_output": {
                    "type": "object",
                    "description": "The structured SLM output to evaluate",
                },
                "workflow_context": {
                    "type": "object",
                    "description": "Additional workflow context",
                },
            },
            "required": ["model_output"],
        },
    },
}


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def _validate_type(value: Any, expected_type: str) -> bool:
    """Validate a value against a JSON schema type string."""
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    expected = type_map.get(expected_type)
    if expected is None:
        return True  # Unknown type — accept
    return isinstance(value, expected)


def _validate_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any],
) -> str | None:
    """
    Validate arguments against a tool's input schema.

    Returns None if valid, or an error message string.
    """
    input_schema = schema.get("input_schema", {})
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])

    # Check required parameters
    for param in required:
        if param not in arguments:
            return f"Missing required parameter: '{param}'"

    # Check for unknown parameters (reject unknown)
    for param in arguments:
        if param not in properties:
            return f"Unknown parameter: '{param}'"

    # Type validation
    for param, value in arguments.items():
        if param in properties:
            expected_type = properties[param].get("type")
            if expected_type and not _validate_type(value, expected_type):
                return (
                    f"Parameter '{param}' has wrong type: "
                    f"expected {expected_type}, got {type(value).__name__}"
                )

    return None


# ---------------------------------------------------------------------------
# Tool handler registry
# ---------------------------------------------------------------------------


def _get_tool_handlers() -> dict[str, Callable]:
    """
    Return mapping of tool names to handler functions.

    Imported lazily to avoid circular imports.
    """
    from dpi_agent.governance.consent import validate_consent
    from dpi_agent.governance.safeguards import apply_risk_gate
    from dpi_agent.registries.database import (
        fetch_credit_profile,
        fetch_land_record,
        verify_identity,
    )

    def _handle_verify_identity(args: dict) -> dict:
        return verify_identity(args["national_id"])

    def _handle_fetch_land_record(args: dict) -> dict:
        return fetch_land_record(args["national_id"])

    def _handle_assess_credit_profile(args: dict) -> dict:
        return fetch_credit_profile(args["national_id"])

    def _handle_check_consent(args: dict) -> dict:
        return validate_consent(
            args["national_id"],
            args["required_permissions"],
            args["workflow_purpose"],
        )

    def _handle_evaluate_subsidy_eligibility(args: dict) -> dict:
        from dpi_agent.config import MINIMUM_LAND_AREA_ACRES

        land_area = args.get("land_area_acres", 0)
        identity_status = args.get("identity_status", "UNKNOWN")

        eligible = identity_status == "ACTIVE" and land_area >= MINIMUM_LAND_AREA_ACRES

        reasons = []
        if identity_status != "ACTIVE":
            reasons.append(f"Identity status is {identity_status}, must be ACTIVE")
        if land_area < MINIMUM_LAND_AREA_ACRES:
            reasons.append(
                f"Land area {land_area} acres is below minimum "
                f"{MINIMUM_LAND_AREA_ACRES} acres (prototype policy rule)"
            )

        return {
            "success": True,
            "data": {
                "eligible": eligible,
                "national_id": args["national_id"],
                "land_area_acres": land_area,
                "minimum_required_acres": MINIMUM_LAND_AREA_ACRES,
                "identity_status": identity_status,
                "reasons": reasons if not eligible else ["All criteria met"],
                "policy_note": "Prototype policy rule — not real government policy",
            },
        }

    def _handle_create_legacy_transaction(args: dict) -> dict:
        from dpi_agent.registries.legacy_adapter import create_legacy_transaction

        return create_legacy_transaction(
            national_id=args["national_id"],
            service_requested=args["service_requested"],
            consent_token_used=args["consent_token_used"],
            slm_confidence_score=args["slm_confidence_score"],
            execution_status=args["execution_status"],
        )

    def _handle_risk_check(args: dict) -> dict:
        return apply_risk_gate(
            args["model_output"],
            args.get("workflow_context"),
        )

    return {
        "verify_identity": _handle_verify_identity,
        "fetch_land_record": _handle_fetch_land_record,
        "assess_credit_profile": _handle_assess_credit_profile,
        "check_consent": _handle_check_consent,
        "evaluate_subsidy_eligibility": _handle_evaluate_subsidy_eligibility,
        "create_legacy_transaction": _handle_create_legacy_transaction,
        "risk_check": _handle_risk_check,
    }


# ---------------------------------------------------------------------------
# Main execution router
# ---------------------------------------------------------------------------


def execute_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a tool by name with validated arguments.

    The router:
    1. Validates tool name against allowlist
    2. Validates arguments against JSON schema
    3. Routes to the correct handler function
    4. Returns structured JSON response
    5. NEVER executes arbitrary code

    Returns:
        {"success": True/False, "tool": "...", "result": {...}, "error": "..."}
    """
    # Gate 1: Validate tool name against allowlist
    if tool_name not in ALLOWED_TOOLS:
        logger.warning("Rejected unknown tool: %s", tool_name)
        return {
            "success": False,
            "tool": tool_name,
            "error": f"Unknown or unauthorized tool: '{tool_name}'",
        }

    # Gate 2: Check schema exists
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        logger.warning("No schema defined for tool: %s", tool_name)
        return {
            "success": False,
            "tool": tool_name,
            "error": f"No schema defined for tool: '{tool_name}'",
        }

    # Gate 3: Validate arguments
    if not isinstance(arguments, dict):
        return {
            "success": False,
            "tool": tool_name,
            "error": "Arguments must be a JSON object (dict)",
        }

    validation_error = _validate_arguments(arguments, schema)
    if validation_error:
        logger.warning("Argument validation failed for %s: %s", tool_name, validation_error)
        return {
            "success": False,
            "tool": tool_name,
            "error": f"Argument validation failed: {validation_error}",
        }

    # Gate 4: Get handler
    handlers = _get_tool_handlers()
    handler = handlers.get(tool_name)
    if handler is None:
        return {
            "success": False,
            "tool": tool_name,
            "error": f"No handler implemented for tool: '{tool_name}'",
        }

    # Gate 5: Execute handler safely
    try:
        result = handler(arguments)
        return {
            "success": True,
            "tool": tool_name,
            "result": result,
        }
    except Exception as e:
        logger.error("Tool execution error for %s: %s", tool_name, str(e))
        return {
            "success": False,
            "tool": tool_name,
            "error": f"Tool execution failed: {str(e)}",
        }


# ---------------------------------------------------------------------------
# Registry introspection
# ---------------------------------------------------------------------------


def list_available_tools() -> list[dict[str, str]]:
    """Return a list of available tools with their descriptions."""
    return [
        {"name": schema["name"], "description": schema["description"]}
        for schema in TOOL_SCHEMAS.values()
    ]


def get_tool_schema(tool_name: str) -> dict[str, Any] | None:
    """Return the schema for a specific tool, or None."""
    return TOOL_SCHEMAS.get(tool_name)
