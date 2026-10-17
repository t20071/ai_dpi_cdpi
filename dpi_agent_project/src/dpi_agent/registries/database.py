"""
DPI registry layer (the "AI Blocks" that read citizen data).

CSV datasets in data/raw/ are imported into SQLite on first run.  The runtime
queries SQLite; the original CSVs are never modified.

Every function returns a structured dict:
    {"success": True/False, "data": {...}, "error": "..."}
"""

import csv
import logging
import sqlite3
from typing import Any

from dpi_agent.config import CSV_FILES, DATABASE_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mosip_identity_registry (
    national_id         TEXT PRIMARY KEY,
    full_name           TEXT NOT NULL,
    phone_number        TEXT NOT NULL,
    preferred_language  TEXT NOT NULL,
    identity_status     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agriculture_land_registry (
    land_record_id  TEXT PRIMARY KEY,
    national_id     TEXT NOT NULL,
    survey_number   TEXT NOT NULL,
    district        TEXT NOT NULL,
    land_area_acres REAL NOT NULL,
    current_crop    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mifos_credit_registry (
    national_id                TEXT PRIMARY KEY,
    credit_score               INTEGER NOT NULL,
    avg_monthly_turnover_inr   REAL NOT NULL,
    repayment_history_status   TEXT NOT NULL,
    active_gst_billing_status  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS depa_consent_artefacts (
    consent_id              TEXT PRIMARY KEY,
    national_id             TEXT NOT NULL,
    consent_purpose         TEXT NOT NULL,
    granular_permissions    TEXT NOT NULL,
    consent_status          TEXT NOT NULL,
    valid_until             TEXT NOT NULL,
    cryptographic_signature TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_municipal_log (
    transaction_id       TEXT PRIMARY KEY,
    national_id          TEXT NOT NULL,
    service_requested    TEXT NOT NULL,
    timestamp            TEXT NOT NULL,
    consent_token_used   TEXT NOT NULL,
    slm_confidence_score REAL NOT NULL,
    execution_status     TEXT NOT NULL
);
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_agri_national_id
    ON agriculture_land_registry(national_id);
CREATE INDEX IF NOT EXISTS idx_consent_national_id
    ON depa_consent_artefacts(national_id);
CREATE INDEX IF NOT EXISTS idx_legacy_national_id
    ON legacy_municipal_log(national_id);
CREATE INDEX IF NOT EXISTS idx_consent_id
    ON depa_consent_artefacts(consent_id);
CREATE INDEX IF NOT EXISTS idx_land_record_id
    ON agriculture_land_registry(land_record_id);
CREATE INDEX IF NOT EXISTS idx_transaction_id
    ON legacy_municipal_log(transaction_id);
"""

# ---------------------------------------------------------------------------
# Table ↔ CSV column mappings
# ---------------------------------------------------------------------------

_TABLE_COLUMNS = {
    "mosip_identity_registry": [
        "national_id",
        "full_name",
        "phone_number",
        "preferred_language",
        "identity_status",
    ],
    "agriculture_land_registry": [
        "land_record_id",
        "national_id",
        "survey_number",
        "district",
        "land_area_acres",
        "current_crop",
    ],
    "mifos_credit_registry": [
        "national_id",
        "credit_score",
        "avg_monthly_turnover_inr",
        "repayment_history_status",
        "active_gst_billing_status",
    ],
    "depa_consent_artefacts": [
        "consent_id",
        "national_id",
        "consent_purpose",
        "granular_permissions",
        "consent_status",
        "valid_until",
        "cryptographic_signature",
    ],
    "legacy_municipal_log": [
        "transaction_id",
        "national_id",
        "service_requested",
        "timestamp",
        "consent_token_used",
        "slm_confidence_score",
        "execution_status",
    ],
}


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------


def _get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row factory enabled."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def mask_phone(phone: Any) -> str:
    """
    Mask a phone number for privacy: show only the last 4 digits.

    Values in spreadsheet scientific notation (e.g. "9.18171E+11") have lost
    their trailing digits, so they are fully masked rather than showing
    digits that are not part of the real number.
    """
    raw = str(phone or "").strip()
    if "e" in raw.lower():
        return "****"
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) > 4:
        return "****" + digits[-4:]
    return "****"


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def initialize_database() -> None:
    """Create tables and indexes if they do not exist."""
    conn = _get_connection()
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.executescript(_INDEX_SQL)
        conn.commit()
        logger.info("Database schema initialized at %s", DATABASE_PATH)
    finally:
        conn.close()


def _table_has_data(conn: sqlite3.Connection, table: str) -> bool:
    """Check whether a table already contains rows."""
    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
    return cursor.fetchone()[0] > 0


def import_csv_datasets() -> dict[str, int]:
    """
    Import all CSV source datasets into SQLite.

    Idempotent: skips tables that already contain data.
    Returns a dict of {table_name: rows_imported}.
    """
    initialize_database()
    conn = _get_connection()
    result: dict[str, int] = {}
    try:
        for table_name, csv_path in CSV_FILES.items():
            if _table_has_data(conn, table_name):
                logger.info("Table '%s' already populated — skipping import.", table_name)
                result[table_name] = 0
                continue

            if not csv_path.exists():
                logger.warning("CSV file not found: %s", csv_path)
                result[table_name] = 0
                continue

            columns = _TABLE_COLUMNS[table_name]
            placeholders = ", ".join(["?"] * len(columns))
            col_names = ", ".join(columns)
            insert_sql = f"INSERT OR IGNORE INTO {table_name} ({col_names}) VALUES ({placeholders})"

            rows_imported = 0
            with open(csv_path, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                batch: list = []
                for row in reader:
                    values = []
                    for col in columns:
                        val = row.get(col, "").strip()
                        # Type coercions
                        if col in (
                            "land_area_acres",
                            "avg_monthly_turnover_inr",
                            "slm_confidence_score",
                        ):
                            try:
                                val = float(val) if val else 0.0
                            except ValueError:
                                val = 0.0
                        elif col == "credit_score":
                            try:
                                val = int(val) if val else 0
                            except ValueError:
                                val = 0
                        values.append(val)
                    batch.append(tuple(values))
                    if len(batch) >= 1000:
                        conn.executemany(insert_sql, batch)
                        rows_imported += len(batch)
                        batch.clear()
                if batch:
                    conn.executemany(insert_sql, batch)
                    rows_imported += len(batch)

            conn.commit()
            result[table_name] = rows_imported
            logger.info("Imported %d rows into '%s'.", rows_imported, table_name)

    finally:
        conn.close()

    return result


# ---------------------------------------------------------------------------
# AI Block: verify_identity
# ---------------------------------------------------------------------------


def verify_identity(national_id: str) -> dict[str, Any]:
    """
    Verify a citizen identity record from MOSIP registry.

    Returns masked phone number for privacy.
    """
    if not national_id or not isinstance(national_id, str):
        return {"success": False, "error": "Invalid national_id parameter"}

    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT national_id, full_name, phone_number, preferred_language, identity_status "
            "FROM mosip_identity_registry WHERE national_id = ?",
            (national_id.strip(),),
        )
        row = cursor.fetchone()
        if row is None:
            return {"success": False, "error": f"Identity not found for national_id: {national_id}"}

        return {
            "success": True,
            "data": {
                "national_id": row["national_id"],
                "full_name": row["full_name"],
                "phone_number": mask_phone(row["phone_number"]),
                "preferred_language": row["preferred_language"],
                "identity_status": row["identity_status"],
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AI Block: fetch_land_record
# ---------------------------------------------------------------------------


def fetch_land_record(national_id: str) -> dict[str, Any]:
    """Fetch agriculture land record for a citizen."""
    if not national_id or not isinstance(national_id, str):
        return {"success": False, "error": "Invalid national_id parameter"}

    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT land_record_id, national_id, survey_number, district, "
            "land_area_acres, current_crop "
            "FROM agriculture_land_registry WHERE national_id = ?",
            (national_id.strip(),),
        )
        row = cursor.fetchone()
        if row is None:
            return {
                "success": False,
                "error": f"Land record not found for national_id: {national_id}",
            }

        return {
            "success": True,
            "data": {
                "land_record_id": row["land_record_id"],
                "national_id": row["national_id"],
                "survey_number": row["survey_number"],
                "district": row["district"],
                "land_area_acres": row["land_area_acres"],
                "current_crop": row["current_crop"],
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AI Block: fetch_credit_profile
# ---------------------------------------------------------------------------


def fetch_credit_profile(national_id: str) -> dict[str, Any]:
    """Fetch credit profile from Mifos credit registry."""
    if not national_id or not isinstance(national_id, str):
        return {"success": False, "error": "Invalid national_id parameter"}

    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT national_id, credit_score, avg_monthly_turnover_inr, "
            "repayment_history_status, active_gst_billing_status "
            "FROM mifos_credit_registry WHERE national_id = ?",
            (national_id.strip(),),
        )
        row = cursor.fetchone()
        if row is None:
            return {
                "success": False,
                "error": f"Credit profile not found for national_id: {national_id}",
            }

        return {
            "success": True,
            "data": {
                "national_id": row["national_id"],
                "credit_score": row["credit_score"],
                "avg_monthly_turnover_inr": row["avg_monthly_turnover_inr"],
                "repayment_history_status": row["repayment_history_status"],
                "active_gst_billing_status": row["active_gst_billing_status"],
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AI Block: get_consent_records
# ---------------------------------------------------------------------------


def get_consent_records(national_id: str) -> dict[str, Any]:
    """Fetch all DEPA consent artefacts for a citizen."""
    if not national_id or not isinstance(national_id, str):
        return {"success": False, "error": "Invalid national_id parameter"}

    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT consent_id, national_id, consent_purpose, granular_permissions, "
            "consent_status, valid_until, cryptographic_signature "
            "FROM depa_consent_artefacts WHERE national_id = ?",
            (national_id.strip(),),
        )
        rows = cursor.fetchall()
        if not rows:
            return {
                "success": False,
                "error": f"No consent records found for national_id: {national_id}",
            }

        records = []
        for row in rows:
            records.append(
                {
                    "consent_id": row["consent_id"],
                    "national_id": row["national_id"],
                    "consent_purpose": row["consent_purpose"],
                    "granular_permissions": row["granular_permissions"],
                    "consent_status": row["consent_status"],
                    "valid_until": row["valid_until"],
                    "cryptographic_signature": row["cryptographic_signature"],
                }
            )

        return {"success": True, "data": records}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AI Block: get_legacy_transactions
# ---------------------------------------------------------------------------


def get_legacy_transactions(national_id: str) -> dict[str, Any]:
    """Fetch legacy municipal log transactions for a citizen."""
    if not national_id or not isinstance(national_id, str):
        return {"success": False, "error": "Invalid national_id parameter"}

    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT transaction_id, national_id, service_requested, timestamp, "
            "consent_token_used, slm_confidence_score, execution_status "
            "FROM legacy_municipal_log WHERE national_id = ?",
            (national_id.strip(),),
        )
        rows = cursor.fetchall()
        if not rows:
            return {
                "success": False,
                "error": f"No legacy transactions found for national_id: {national_id}",
            }

        records = []
        for row in rows:
            records.append(
                {
                    "transaction_id": row["transaction_id"],
                    "national_id": row["national_id"],
                    "service_requested": row["service_requested"],
                    "timestamp": row["timestamp"],
                    "slm_confidence_score": row["slm_confidence_score"],
                    "execution_status": row["execution_status"],
                }
            )

        return {"success": True, "data": records}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def ensure_database_ready() -> dict[str, int]:
    """Initialize database and import CSVs if needed. Returns import counts."""
    return import_csv_datasets()


def rebuild_database() -> dict[str, int]:
    """Delete the runtime database and re-import every CSV from data/raw/."""
    for suffix in ("", "-wal", "-shm"):
        DATABASE_PATH.with_name(DATABASE_PATH.name + suffix).unlink(missing_ok=True)
    return import_csv_datasets()


def table_counts() -> dict[str, int]:
    """Return the row count of every registry table."""
    conn = _get_connection()
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in _TABLE_COLUMNS
        }
    finally:
        conn.close()
