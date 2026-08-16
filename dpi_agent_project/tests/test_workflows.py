"""Tests for workflow engine."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dpi_registries import ensure_database_ready
from workflow_engine import process_request
from model_client import DeterministicProvider, set_default_provider


class TestWorkflowSetup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_database_ready()
        # Use deterministic provider for all workflow tests
        set_default_provider(DeterministicProvider())


class TestSubsidyWorkflow(TestWorkflowSetup):
    """Test the solar pump subsidy workflow."""

    def test_subsidy_success(self):
        """Scenario A: Valid citizen should succeed."""
        result = process_request(
            "I am Suresh Kaur, ID 765-741-434. "
            "I want to apply for a solar pump subsidy."
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIn("transaction", result)

    def test_subsidy_no_consent(self):
        """Citizen with REVOKED consent should be denied."""
        result = process_request(
            "I am Sanjay Kumar, ID 201-206-655. "
            "I want to apply for a solar pump subsidy."
        )
        # 201-206-655 has REVOKED agri consent
        self.assertFalse(result["success"])


class TestCreditWorkflow(TestWorkflowSetup):
    """Test the agricultural credit workflow."""

    def test_credit_consent_denied(self):
        """Scenario B: Citizen with agri-only consent should be denied credit."""
        result = process_request(
            "I am Anjali Iyer, ID 213-163-577. "
            "I want to assess my credit for an agricultural loan."
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "DENIED_CONSENT")


class TestEscalation(TestWorkflowSetup):
    """Test human escalation triggers."""

    def test_suspended_identity_escalation(self):
        """Scenario C: SUSPENDED identity should trigger escalation."""
        result = process_request(
            "I am Suresh Mishra, ID 715-454-455. "
            "I need help, I'm not sure how to apply for a solar pump subsidy."
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "ESCALATED")
        self.assertIn("escalation", result)
        self.assertIn("human review", result["escalation"]["message"].lower())


class TestLegacyWriteback(TestWorkflowSetup):
    """Test +1 legacy write-back."""

    def test_successful_workflow_creates_transaction(self):
        """Successful workflow should create a legacy transaction."""
        result = process_request(
            "I am Suresh Kaur, ID 765-741-434. "
            "I want to apply for a solar pump subsidy."
        )
        if result.get("success"):
            self.assertIn("transaction", result)
            txn = result["transaction"]
            self.assertIn("transaction_id", txn)
            self.assertTrue(txn["transaction_id"].startswith("TXN-"))


class TestValidationFailures(TestWorkflowSetup):
    """Test schema validation failures."""

    def test_empty_request(self):
        """Empty request should still produce a result (deterministic provider handles it)."""
        result = process_request("")
        # The deterministic provider will still generate output,
        # but with low confidence likely leading to escalation
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
