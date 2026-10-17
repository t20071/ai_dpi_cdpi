"""
Sovereign DPI-AI Public Agent — DEPA Consent Engine

Prototype simulation of consent governance based on India's Data Empowerment
and Protection Architecture (DEPA) framework.

DISCLAIMER: This is a SIMULATED consent-validation engine for prototype
demonstration purposes. It is NOT a production government credential-validation
mechanism and does NOT implement real DEPA protocol specifications.

Five consent gates:
    1. Status gate     — Only ACTIVE consents pass
    2. Expiry gate     — valid_until must be > current UTC time
    3. Granularity gate — Requested data category must be in granular_permissions
    4. Purpose gate    — consent_purpose must align with workflow purpose
    5. Signature gate  — SHA-256 integrity verification (simulated)

Every validation creates an audit record in consent_audit_log.jsonl.
"""

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any

from dpi_agent.audit import consent_log
from dpi_agent.config import WORKFLOW_CONSENT_PURPOSES, utc_now
from dpi_agent.registries.database import get_consent_records

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission mappings: data category → required permission string
# ---------------------------------------------------------------------------

DATA_CATEGORY_PERMISSIONS = {
    "identity": "identity:read",
    "agriculture_land": "agri_land:read",
    "agri_land": "agri_land:read",
    "mifos_credit": "mifos_credit:read",
    "credit": "mifos_credit:read",
}


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def _write_consent_audit(
    consent_id: str | None,
    national_id: str,
    requested_data: str,
    purpose: str,
    result: str,
    reason: str,
) -> None:
    """Append a structured audit record to the consent audit log."""
    consent_log.append(
        {
            "consent_id": consent_id or "N/A",
            "national_id": national_id,
            "requested_data": requested_data,
            "purpose": purpose,
            "result": result,
            "reason": reason,
        }
    )
    logger.info("Consent audit: %s — %s — %s", national_id, result, reason)


# ---------------------------------------------------------------------------
# Gate 1: Status check
# ---------------------------------------------------------------------------


def _check_status(consent: dict[str, Any]) -> str | None:
    """Return None if status is ACTIVE, otherwise return denial reason."""
    status = consent.get("consent_status", "UNKNOWN").upper()
    if status == "ACTIVE":
        return None
    if status == "REVOKED":
        return "CONSENT_REVOKED"
    if status == "EXPIRED":
        return "CONSENT_STATUS_EXPIRED"
    return f"CONSENT_STATUS_INVALID: {status}"


# ---------------------------------------------------------------------------
# Gate 2: Expiry check
# ---------------------------------------------------------------------------


def _check_expiry(consent: dict[str, Any]) -> str | None:
    """Return None if consent is not expired, otherwise return denial reason."""
    valid_until_str = consent.get("valid_until", "")
    if not valid_until_str:
        return "CONSENT_NO_EXPIRY_DATE"

    try:
        # Parse the ISO timestamp; assume UTC if no timezone info
        valid_until = datetime.fromisoformat(valid_until_str)
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=UTC)
        if utc_now() > valid_until:
            return "CONSENT_EXPIRED"
    except ValueError:
        return "CONSENT_INVALID_EXPIRY_FORMAT"

    return None


# ---------------------------------------------------------------------------
# Gate 3: Granularity check
# ---------------------------------------------------------------------------


def _check_granular_permissions(
    consent: dict[str, Any],
    required_permissions: list[str],
) -> str | None:
    """
    Return None if all required permissions are granted.
    Otherwise return denial reason.

    required_permissions: list of data category keys (e.g., ["agri_land", "mifos_credit"])
    """
    granted_raw = consent.get("granular_permissions", "")
    # Parse comma-separated permissions
    granted_set = {p.strip() for p in granted_raw.split(",") if p.strip()}

    for req in required_permissions:
        # Resolve category to permission string
        perm_string = DATA_CATEGORY_PERMISSIONS.get(req, req)
        if perm_string not in granted_set:
            return f"GRANULAR_PERMISSION_MISSING: {req} (need '{perm_string}')"

    return None


# ---------------------------------------------------------------------------
# Gate 4: Purpose limitation check
# ---------------------------------------------------------------------------


def _check_purpose(
    consent: dict[str, Any],
    workflow_purpose: str,
) -> str | None:
    """
    Return None if consent purpose aligns with the workflow.
    Otherwise return denial reason.
    """
    consent_purpose = consent.get("consent_purpose", "")
    allowed_purposes = WORKFLOW_CONSENT_PURPOSES.get(workflow_purpose, [])

    if consent_purpose in allowed_purposes:
        return None

    # Also allow exact match
    if consent_purpose == workflow_purpose:
        return None

    return f"PURPOSE_MISMATCH: consent='{consent_purpose}', workflow='{workflow_purpose}'"


# ---------------------------------------------------------------------------
# Gate 5: Signature integrity check (SIMULATED)
# ---------------------------------------------------------------------------

# Fields covered by the artefact signature, in signing order.
_SIGNED_FIELDS = (
    "consent_id",
    "national_id",
    "consent_purpose",
    "granular_permissions",
    "consent_status",
    "valid_until",
)


def compute_consent_signature(consent: dict[str, Any]) -> str:
    """
    Compute the SHA-256 digest for a consent artefact.

    The bundled dataset signs the plain concatenation (no separator) of
    _SIGNED_FIELDS; every one of its 10,000 artefacts verifies this way.
    """
    payload = "".join(str(consent.get(f, "")) for f in _SIGNED_FIELDS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _verify_signature(consent: dict[str, Any]) -> str | None:
    """
    Simulated cryptographic integrity verification using SHA-256.

    DISCLAIMER: This is NOT production-grade cryptographic verification.
    An unkeyed hash detects edits to a consent artefact (e.g. a status
    flipped from REVOKED to ACTIVE, or a permission added) but not a forger
    who recomputes the hash.  A real DEPA consent manager signs artefacts
    with a private key and verifiers check against its public key.
    """
    stored_sig = consent.get("cryptographic_signature", "")
    if not stored_sig:
        return "SIGNATURE_MISSING"

    stored_sig = stored_sig.strip().lower()
    if len(stored_sig) != 64 or any(c not in "0123456789abcdef" for c in stored_sig):
        return "SIGNATURE_INVALID_FORMAT"

    if not hmac.compare_digest(stored_sig, compute_consent_signature(consent)):
        return "SIGNATURE_VERIFICATION_FAILED"

    return None


# ---------------------------------------------------------------------------
# Main consent validation
# ---------------------------------------------------------------------------


def validate_consent(
    national_id: str,
    required_permissions: list[str],
    workflow_purpose: str,
) -> dict[str, Any]:
    """
    Validate DEPA consent for a citizen's data access request.

    Args:
        national_id: The citizen's national identifier
        required_permissions: List of data categories needed
            (e.g., ["agri_land"], ["mifos_credit"], ["agri_land", "mifos_credit"])
        workflow_purpose: The workflow intent name
            (e.g., "apply_for_solar_pump_subsidy")

    Returns:
        {
            "valid": True/False,
            "consent_id": "...",
            "reason": "...",
            "details": {...}
        }
    """
    if not national_id:
        _write_consent_audit(
            None,
            national_id,
            str(required_permissions),
            workflow_purpose,
            "DENIED",
            "INVALID_NATIONAL_ID",
        )
        return {
            "valid": False,
            "consent_id": None,
            "reason": "INVALID_NATIONAL_ID",
            "details": {"error": "national_id is required"},
        }

    # Fetch all consent records for this citizen
    consent_result = get_consent_records(national_id)
    if not consent_result.get("success"):
        _write_consent_audit(
            None,
            national_id,
            str(required_permissions),
            workflow_purpose,
            "DENIED",
            "NO_CONSENT_RECORDS",
        )
        return {
            "valid": False,
            "consent_id": None,
            "reason": "NO_CONSENT_RECORDS",
            "details": {"error": consent_result.get("error", "No consent records found")},
        }

    # Try to find at least one consent that passes all gates
    records = consent_result["data"]
    all_failures: list[dict[str, str]] = []

    for consent in records:
        consent_id = consent.get("consent_id", "UNKNOWN")
        failures: list[str] = []

        # Gate 1: Status
        status_err = _check_status(consent)
        if status_err:
            failures.append(status_err)

        # Gate 2: Expiry
        expiry_err = _check_expiry(consent)
        if expiry_err:
            failures.append(expiry_err)

        # Gate 3: Granularity
        perm_err = _check_granular_permissions(consent, required_permissions)
        if perm_err:
            failures.append(perm_err)

        # Gate 4: Purpose
        purpose_err = _check_purpose(consent, workflow_purpose)
        if purpose_err:
            failures.append(purpose_err)

        # Gate 5: Signature
        sig_err = _verify_signature(consent)
        if sig_err:
            failures.append(sig_err)

        if not failures:
            # All gates passed
            _write_consent_audit(
                consent_id,
                national_id,
                str(required_permissions),
                workflow_purpose,
                "GRANTED",
                "ALL_GATES_PASSED",
            )
            return {
                "valid": True,
                "consent_id": consent_id,
                "reason": "ALL_GATES_PASSED",
                "details": {
                    "consent_purpose": consent.get("consent_purpose"),
                    "granular_permissions": consent.get("granular_permissions"),
                    "valid_until": consent.get("valid_until"),
                },
            }

        all_failures.append(
            {
                "consent_id": consent_id,
                "failures": failures,
            }
        )

    # No valid consent found — report the most relevant failure
    primary_failure = (
        all_failures[0] if all_failures else {"consent_id": "N/A", "failures": ["UNKNOWN"]}
    )
    primary_reason = primary_failure["failures"][0] if primary_failure["failures"] else "UNKNOWN"

    _write_consent_audit(
        primary_failure.get("consent_id"),
        national_id,
        str(required_permissions),
        workflow_purpose,
        "DENIED",
        primary_reason,
    )

    return {
        "valid": False,
        "consent_id": primary_failure.get("consent_id"),
        "reason": primary_reason,
        "details": {
            "all_failures": all_failures,
            "total_consents_checked": len(records),
        },
    }


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def check_agriculture_consent(national_id: str, workflow_purpose: str) -> dict[str, Any]:
    """Shortcut to validate consent for agriculture land data access."""
    return validate_consent(national_id, ["agri_land"], workflow_purpose)


def check_credit_consent(national_id: str, workflow_purpose: str) -> dict[str, Any]:
    """Shortcut to validate consent for credit data access."""
    return validate_consent(national_id, ["mifos_credit"], workflow_purpose)


def check_full_subsidy_consent(national_id: str, workflow_purpose: str) -> dict[str, Any]:
    """Shortcut to validate consent for both agri land and credit data."""
    return validate_consent(national_id, ["agri_land", "mifos_credit"], workflow_purpose)
