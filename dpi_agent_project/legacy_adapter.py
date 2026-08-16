"""
Sovereign DPI-AI Public Agent — +1 Legacy Adapter

Demonstrates the CDPI "+1 approach" — extending existing DPI infrastructure
with an AI layer while preserving the original registry architecture.

The legacy adapter:
    - Does NOT modify or replace the original registry architecture
    - Creates new transactions in SQLite (legacy_municipal_log table)
    - Writes a SEPARATE output CSV (legacy_municipal_log_output.csv)
    - Never corrupts the original input dataset
    - Uses append-safe mechanisms
"""

import csv
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from config import (
    DATABASE_PATH,
    LEGACY_OUTPUT_CSV,
    WORKFLOW_AUDIT_LOG,
)

logger = logging.getLogger(__name__)

# Output CSV columns
_OUTPUT_COLUMNS = [
    "transaction_id",
    "national_id",
    "service_requested",
    "timestamp",
    "consent_token_used",
    "slm_confidence_score",
    "execution_status",
]


def _generate_transaction_id() -> str:
    """Generate a unique transaction ID with TXN- prefix."""
    short_uuid = uuid.uuid4().hex[:8].upper()
    return f"TXN-{short_uuid}"


def create_legacy_transaction(
    national_id: str,
    service_requested: str,
    consent_token_used: str,
    slm_confidence_score: float,
    execution_status: str,
) -> Dict[str, Any]:
    """
    Create a new transaction in the +1 legacy municipal system.

    This demonstrates the "+1 approach" from CDPI:
    - Writes to SQLite (legacy_municipal_log table)
    - Also appends to a SEPARATE output CSV file
    - Never modifies the original legacy_municipal_log.csv

    Args:
        national_id: Citizen's national identifier
        service_requested: Description of the service
        consent_token_used: The consent token/ID used
        slm_confidence_score: Model confidence score
        execution_status: Status (COMPLETED, ESCALATED_TO_HUMAN, etc.)

    Returns:
        Structured result with transaction_id and confirmation
    """
    import sqlite3

    transaction_id = _generate_transaction_id()
    timestamp = datetime.now(timezone.utc).isoformat()

    # Validate inputs
    if not national_id or not isinstance(national_id, str):
        return {"success": False, "error": "Invalid national_id"}
    if not service_requested:
        return {"success": False, "error": "service_requested is required"}

    # --- Write to SQLite ---
    try:
        conn = sqlite3.connect(str(DATABASE_PATH))
        conn.execute(
            "INSERT INTO legacy_municipal_log "
            "(transaction_id, national_id, service_requested, timestamp, "
            "consent_token_used, slm_confidence_score, execution_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                transaction_id,
                national_id,
                service_requested,
                timestamp,
                consent_token_used,
                slm_confidence_score,
                execution_status,
            ),
        )
        conn.commit()
        conn.close()
        logger.info("Legacy transaction %s written to SQLite.", transaction_id)
    except Exception as e:
        logger.error("Failed to write legacy transaction to SQLite: %s", str(e))
        return {
            "success": False,
            "error": f"Database write failed: {str(e)}",
            "transaction_id": transaction_id,
        }

    # --- Append to separate output CSV (never modify original) ---
    try:
        LEGACY_OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        file_exists = LEGACY_OUTPUT_CSV.exists()

        with open(LEGACY_OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_OUTPUT_COLUMNS)
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "transaction_id": transaction_id,
                "national_id": national_id,
                "service_requested": service_requested,
                "timestamp": timestamp,
                "consent_token_used": consent_token_used,
                "slm_confidence_score": slm_confidence_score,
                "execution_status": execution_status,
            })
        logger.info("Legacy transaction %s appended to output CSV.", transaction_id)
    except Exception as e:
        logger.warning("Failed to append to output CSV (SQLite write succeeded): %s", str(e))

    return {
        "success": True,
        "data": {
            "transaction_id": transaction_id,
            "national_id": national_id,
            "service_requested": service_requested,
            "timestamp": timestamp,
            "execution_status": execution_status,
            "slm_confidence_score": slm_confidence_score,
        },
    }
