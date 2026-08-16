"""
Sovereign DPI-AI Public Agent — Interactive Terminal Application

A local DPI-AI architectural prototype demonstrating Public Agents, AI Blocks,
DPI Workflows, consent-based data access, risk gates, human escalation, and
"+1" legacy integration.

DISCLAIMER: This is a LOCAL PROTOTYPE, not a production government system.
All runtime data and model inference remain entirely local.
"""

import json
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    CONSENT_AUDIT_LOG,
    WORKFLOW_AUDIT_LOG,
    ESCALATIONS_LOG,
    AUDIT_DIRECTORY,
    DATABASE_PATH,
    OLLAMA_MODEL,
    LLM_PROVIDER,
)
from dpi_registries import ensure_database_ready
from model_client import health_check, get_provider, set_default_provider
from workflow_engine import (
    process_request,
    run_scenario_a,
    run_scenario_b,
    run_scenario_c,
)

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BANNER = r"""
====================================================
 SOVEREIGN DPI-AI PUBLIC AGENT
====================================================
 Local Prototype — NOT a production government system
 All data and inference remain on this device.
====================================================
"""

MENU = """
 1. Run Scenario A — Subsidy Success
 2. Run Scenario B — DEPA Consent Failure
 3. Run Scenario C — Human Escalation
 4. Interactive Public Agent
 5. Inspect Consent Audit
 6. Inspect Workflow Audit
 7. Inspect Escalations
 8. Check Ollama
 9. Exit
"""


def _print_result(result: dict) -> None:
    """Print a workflow result summary."""
    print("\n" + "-" * 60)
    if result.get("success"):
        print("  [OK] WORKFLOW COMPLETED SUCCESSFULLY")
        if result.get("message"):
            print(f"\n  {result['message']}")
    else:
        status = result.get("status", "UNKNOWN")
        print(f"  X WORKFLOW STATUS: {status}")
        if result.get("message"):
            print(f"\n  {result['message']}")
        if result.get("escalation"):
            esc = result["escalation"]
            print(f"\n  {esc.get('message', '')}")
            if esc.get("reason"):
                print(f"  Reason: {esc['reason']}")
    print("-" * 60)


def _inspect_log(log_path: Path, label: str, max_lines: int = 20) -> None:
    """Display the last N entries from a JSONL audit log."""
    print(f"\n{'-' * 60}")
    print(f"  {label}")
    print(f"  File: {log_path}")
    print(f"{'-' * 60}")

    if not log_path.exists():
        print("  (No log entries yet)")
        return

    lines = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)

    if not lines:
        print("  (Log file is empty)")
        return

    print(f"  Showing last {min(len(lines), max_lines)} of {len(lines)} entries:\n")

    for line in lines[-max_lines:]:
        try:
            record = json.loads(line)
            # Pretty-print key fields
            timestamp = record.get("timestamp", "")[:19]
            if "consent_id" in record:
                # Consent audit
                print(f"  [{timestamp}] {record.get('result', '?'):>7} | "
                      f"NID: {record.get('national_id', '?')} | "
                      f"Data: {record.get('requested_data', '?')} | "
                      f"Reason: {record.get('reason', '?')}")
            elif "workflow_id" in record:
                # Workflow audit
                print(f"  [{timestamp}] {record.get('final_status', '?'):>20} | "
                      f"WF: {record.get('workflow_id', '?')} | "
                      f"Intent: {record.get('intent', '?')}")
            elif "case_id" in record:
                # Escalation
                print(f"  [{timestamp}] {record.get('case_id', '?')} | "
                      f"NID: {record.get('national_id', '?')} | "
                      f"Decision: {record.get('decision', '?')} | "
                      f"Reason: {record.get('reason', '?')}")
            else:
                print(f"  {json.dumps(record, indent=2)[:200]}")
        except json.JSONDecodeError:
            print(f"  (malformed entry)")

    print()


def _check_ollama() -> None:
    """Check Ollama connectivity and model availability."""
    print(f"\n{'-' * 60}")
    print("  OLLAMA HEALTH CHECK")
    print(f"{'-' * 60}")

    status = health_check()
    if status.get("available"):
        print(f"  [OK] Ollama is running at {status.get('host', '?')}")
        if status.get("model_loaded"):
            print(f"  [OK] Model '{status.get('model', '?')}' is available")
        else:
            print(f"  X Model '{status.get('model', '?')}' not found")
            print(f"    Run: ollama pull {OLLAMA_MODEL}")
        if status.get("models"):
            print(f"  Available models: {', '.join(status['models'][:5])}")
    else:
        print(f"  X Ollama is NOT available")
        print(f"    Error: {status.get('error', 'Unknown')}")
        print(f"    Make sure Ollama is running: ollama serve")
    print()


def _interactive_mode() -> None:
    """Run the interactive Public Agent mode."""
    print(f"\n{'═' * 60}")
    print("  INTERACTIVE PUBLIC AGENT")
    print(f"{'═' * 60}")
    print("  Type your request in natural language.")
    print("  Type 'quit' or 'exit' to return to menu.")
    print("  Type 'live' to toggle live SLM mode.")
    print()

    use_live = False

    # Check Ollama availability
    status = health_check()
    if status.get("available") and status.get("model_loaded"):
        use_live = True
        print("  [SLM Mode: LIVE — using Ollama]")
    else:
        print("  [SLM Mode: DETERMINISTIC — Ollama not available]")
        print("  (Install and run Ollama with the model for live SLM mode)")
    print()

    while True:
        try:
            user_input = input("  Citizen > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Returning to menu...")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break
        if user_input.lower() == "live":
            use_live = not use_live
            mode = "LIVE" if use_live else "DETERMINISTIC"
            print(f"  [SLM Mode switched to: {mode}]")
            continue

        result = process_request(user_input, use_live_slm=use_live)
        _print_result(result)
        print()


def main() -> None:
    """Main entry point for the Sovereign DPI-AI Public Agent."""
    print(BANNER)

    # Initialize database
    print("  Initializing DPI registries...")
    try:
        counts = ensure_database_ready()
        total = sum(counts.values())
        if total > 0:
            print(f"  [OK] Imported {total:,} records into SQLite")
            for table, count in counts.items():
                if count > 0:
                    print(f"    • {table}: {count:,} rows")
        else:
            print("  [OK] Database already initialized")
        print(f"  [OK] Database: {DATABASE_PATH}")
    except Exception as e:
        print(f"  X Database initialization failed: {e}")
        sys.exit(1)

    # Ensure audit directory
    AUDIT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    print(f"  [OK] Audit directory: {AUDIT_DIRECTORY}")

    print(f"  [OK] LLM Provider: {LLM_PROVIDER}")
    print()

    while True:
        print(MENU)
        try:
            choice = input("  Select option > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Exiting...")
            break

        if choice == "1":
            result = run_scenario_a()
            _print_result(result)
        elif choice == "2":
            result = run_scenario_b()
            _print_result(result)
        elif choice == "3":
            result = run_scenario_c()
            _print_result(result)
        elif choice == "4":
            _interactive_mode()
        elif choice == "5":
            _inspect_log(CONSENT_AUDIT_LOG, "CONSENT AUDIT LOG")
        elif choice == "6":
            _inspect_log(WORKFLOW_AUDIT_LOG, "WORKFLOW AUDIT LOG")
        elif choice == "7":
            _inspect_log(ESCALATIONS_LOG, "ESCALATIONS LOG")
        elif choice == "8":
            _check_ollama()
        elif choice == "9":
            print("\n  Exiting Sovereign DPI-AI Public Agent. Goodbye.")
            break
        else:
            print("  Invalid option. Please select 1-9.")


if __name__ == "__main__":
    main()
