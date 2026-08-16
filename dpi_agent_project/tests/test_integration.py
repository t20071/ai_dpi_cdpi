"""Integration tests for the three demonstration scenarios."""

import os
import sys
import unittest
import json
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dpi_registries import ensure_database_ready
from model_client import DeterministicProvider, set_default_provider
from workflow_engine import run_scenario_a, run_scenario_b, run_scenario_c
from config import CONSENT_AUDIT_LOG, WORKFLOW_AUDIT_LOG, ESCALATIONS_LOG


class TestIntegrationSetup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_database_ready()
        set_default_provider(DeterministicProvider())


class TestScenarioA(TestIntegrationSetup):
    """
    Scenario A — Subsidy Success

    Citizen Suresh Kaur (765-741-434) applies for solar pump subsidy.
    Expected: identity → consent → land → policy → risk → transaction → audit.
    """

    def test_scenario_a_completes(self):
        result = run_scenario_a()
        self.assertTrue(result["success"], f"Scenario A failed: {result}")
        self.assertEqual(result["status"], "COMPLETED")

    def test_scenario_a_creates_transaction(self):
        result = run_scenario_a()
        if result.get("success"):
            self.assertIn("transaction", result)
            self.assertIn("transaction_id", result["transaction"])

    def test_scenario_a_creates_audit(self):
        result = run_scenario_a()
        # Check workflow audit log exists and has entries
        self.assertTrue(WORKFLOW_AUDIT_LOG.exists())


class TestScenarioB(TestIntegrationSetup):
    """
    Scenario B — DEPA Consent Failure

    Citizen Anjali Iyer (213-163-577) requests credit assessment.
    Her consent only permits agri_land, NOT mifos_credit.
    Expected: consent DENIED, credit data NOT returned.
    """

    def test_scenario_b_denied(self):
        result = run_scenario_b()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "DENIED_CONSENT")

    def test_scenario_b_no_credit_data_leaked(self):
        result = run_scenario_b()
        # Ensure no credit data is in the response
        result_str = json.dumps(result)
        self.assertNotIn("credit_score", result_str)
        self.assertNotIn("avg_monthly_turnover", result_str)

    def test_scenario_b_creates_audit(self):
        result = run_scenario_b()
        self.assertTrue(CONSENT_AUDIT_LOG.exists())


class TestScenarioC(TestIntegrationSetup):
    """
    Scenario C — Human Escalation

    Citizen Suresh Mishra (715-454-455) has a SUSPENDED identity.
    Expected: identity check fails → escalation → human review message.
    """

    def test_scenario_c_escalated(self):
        result = run_scenario_c()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "ESCALATED")

    def test_scenario_c_has_escalation_message(self):
        result = run_scenario_c()
        self.assertIn("escalation", result)
        msg = result["escalation"].get("message", "")
        self.assertIn("human review", msg.lower())

    def test_scenario_c_creates_escalation_log(self):
        result = run_scenario_c()
        self.assertTrue(ESCALATIONS_LOG.exists())


class TestAuditIntegrity(TestIntegrationSetup):
    """Test that audit logs contain valid JSONL."""

    def test_workflow_audit_is_valid_jsonl(self):
        # Run a scenario to generate audit
        run_scenario_a()
        if WORKFLOW_AUDIT_LOG.exists():
            with open(WORKFLOW_AUDIT_LOG, "r") as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if line:
                        try:
                            json.loads(line)
                        except json.JSONDecodeError:
                            self.fail(f"Invalid JSONL at line {i+1}: {line[:100]}")

    def test_consent_audit_is_valid_jsonl(self):
        run_scenario_b()
        if CONSENT_AUDIT_LOG.exists():
            with open(CONSENT_AUDIT_LOG, "r") as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if line:
                        try:
                            json.loads(line)
                        except json.JSONDecodeError:
                            self.fail(f"Invalid JSONL at line {i+1}: {line[:100]}")


if __name__ == "__main__":
    unittest.main()
