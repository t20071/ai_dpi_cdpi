"""
Sovereign DPI-AI Public Agent — Central Configuration

All configurable parameters for the prototype are defined here.
Values can be overridden via environment variables or a .env file.

IMPORTANT DISCLAIMERS:
- This is a LOCAL PROTOTYPE, not a production government system.
- The CONFIDENCE_THRESHOLD of 0.80 is an application-specific prototype policy.
  It is NOT a threshold prescribed by NIST AI RMF.
- Policy rules (e.g., minimum land area) are prototype demonstration values.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


def _load_dotenv(env_path: Optional[Path] = None) -> None:
    """Load .env file into os.environ if it exists. Minimal implementation."""
    if env_path is None:
        env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value


# Load .env on import
_load_dotenv()


# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.resolve()
DATA_FOLDER = PROJECT_ROOT / "data_folder"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(DATA_FOLDER / "simulated_dpi.db")))
AUDIT_DIRECTORY = Path(os.getenv("AUDIT_DIRECTORY", str(PROJECT_ROOT / "audit")))

# ---------------------------------------------------------------------------
# LLM / Ollama configuration
# ---------------------------------------------------------------------------

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")  # "ollama", "cloud", "deterministic"

# ---------------------------------------------------------------------------
# Confidence threshold
# ---------------------------------------------------------------------------
# APPLICATION-SPECIFIC PROTOTYPE POLICY.
# This 0.80 value is NOT a threshold prescribed by NIST AI RMF.
# It is a configurable parameter chosen for this prototype demonstration.

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.80"))

# ---------------------------------------------------------------------------
# Prototype policy rules
# ---------------------------------------------------------------------------
# These are PROTOTYPE DEMONSTRATION values, not real government policy.

MINIMUM_LAND_AREA_ACRES = float(os.getenv("MINIMUM_LAND_AREA_ACRES", "2.0"))
MINIMUM_CREDIT_SCORE = int(os.getenv("MINIMUM_CREDIT_SCORE", "500"))
HIGH_IMPACT_CONFIDENCE_THRESHOLD = float(
    os.getenv("HIGH_IMPACT_CONFIDENCE_THRESHOLD", "0.90")
)

# ---------------------------------------------------------------------------
# CSV source files (original datasets — never modified at runtime)
# ---------------------------------------------------------------------------

CSV_FILES = {
    "mosip_identity_registry": DATA_FOLDER / "mosip_identity_registry.csv",
    "agriculture_land_registry": DATA_FOLDER / "agriculture_land_registry.csv",
    "mifos_credit_registry": DATA_FOLDER / "mifos_credit_registry.csv",
    "depa_consent_artefacts": DATA_FOLDER / "depa_consent_artefacts.csv",
    "legacy_municipal_log": DATA_FOLDER / "legacy_municipal_log.csv",
}

# Legacy output file (separate from original CSV)
LEGACY_OUTPUT_CSV = DATA_FOLDER / "legacy_municipal_log_output.csv"

# ---------------------------------------------------------------------------
# Audit log files
# ---------------------------------------------------------------------------

CONSENT_AUDIT_LOG = AUDIT_DIRECTORY / "consent_audit_log.jsonl"
WORKFLOW_AUDIT_LOG = AUDIT_DIRECTORY / "workflow_audit_log.jsonl"
ESCALATIONS_LOG = AUDIT_DIRECTORY / "escalations_log.jsonl"

# ---------------------------------------------------------------------------
# Allowed tool names (security allowlist)
# ---------------------------------------------------------------------------

ALLOWED_TOOLS = frozenset([
    "verify_identity",
    "fetch_land_record",
    "assess_credit_profile",
    "check_consent",
    "evaluate_subsidy_eligibility",
    "create_legacy_transaction",
    "risk_check",
])

# ---------------------------------------------------------------------------
# Supported workflow intents
# ---------------------------------------------------------------------------

SUPPORTED_WORKFLOWS = frozenset([
    "apply_for_solar_pump_subsidy",
    "assess_agricultural_credit",
])

# ---------------------------------------------------------------------------
# Consent purpose mappings
# ---------------------------------------------------------------------------
# Maps workflow intents to acceptable consent purposes

WORKFLOW_CONSENT_PURPOSES = {
    "apply_for_solar_pump_subsidy": [
        "Agricultural Support",
        "Subsidy Screening",
    ],
    "assess_agricultural_credit": [
        "Credit Assessment",
        "Micro-Loan Processing",
        "Subsidy Screening",
    ],
}

# ---------------------------------------------------------------------------
# Scenario test citizens (discovered from actual dataset)
# ---------------------------------------------------------------------------

SCENARIO_CITIZENS = {
    "scenario_a": {
        "national_id": "765-741-434",
        "name": "Suresh Kaur",
        "description": "Subsidy success — ACTIVE identity, ACTIVE agri consent, 12.8 acres",
    },
    "scenario_b": {
        "national_id": "213-163-577",
        "name": "Anjali Iyer",
        "description": "DEPA failure — consent allows agri_land only, credit request denied",
    },
    "scenario_c": {
        "national_id": "715-454-455",
        "name": "Suresh Mishra",
        "description": "Human escalation — SUSPENDED identity triggers escalation",
    },
}
