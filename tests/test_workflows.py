"""Tests for workflow engine."""

import json
import unittest

from dpi_agent.config import ESCALATIONS_LOG
from dpi_agent.llm.providers import DeterministicProvider, set_default_provider
from dpi_agent.registries.database import ensure_database_ready
from dpi_agent.workflows.engine import process_request


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
            "I am Suresh Kaur, ID 765-741-434. I want to apply for a solar pump subsidy."
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIn("transaction", result)

    def test_subsidy_no_consent(self):
        """Citizen with REVOKED consent should be denied."""
        result = process_request(
            "I am Sanjay Kumar, ID 201-206-655. I want to apply for a solar pump subsidy."
        )
        # 201-206-655 has REVOKED agri consent
        self.assertFalse(result["success"])


class TestCreditWorkflow(TestWorkflowSetup):
    """Test the agricultural credit workflow."""

    def test_credit_consent_denied(self):
        """Scenario B: Citizen with agri-only consent should be denied credit."""
        result = process_request(
            "I am Anjali Iyer, ID 213-163-577. I want to assess my credit for an agricultural loan."
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
            "I am Suresh Kaur, ID 765-741-434. I want to apply for a solar pump subsidy."
        )
        if result.get("success"):
            self.assertIn("transaction", result)
            txn = result["transaction"]
            self.assertIn("transaction_id", txn)
            self.assertTrue(txn["transaction_id"].startswith("TXN-"))


def _last_escalation():
    """Return the most recent record in the escalations log."""
    with open(ESCALATIONS_LOG, encoding="utf-8") as f:
        return json.loads(f.read().strip().splitlines()[-1])


class _FixedOutputProvider(DeterministicProvider):
    """Stand-in for a live SLM that returns a fixed plan."""

    def __init__(self, **overrides):
        self.overrides = overrides

    @property
    def name(self):
        return "fixed-test-model"

    def _plan(self, text):
        output = super()._plan(text)
        output.update(self.overrides)
        return output


class TestValidationFailures(TestWorkflowSetup):
    """Test schema validation failures."""

    def test_empty_request(self):
        """Empty request asks for a national ID."""
        result = process_request("")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "NEEDS_NATIONAL_ID")

    def test_boolean_confidence_rejected(self):
        """confidence=true must not be accepted as 1.0."""
        result = process_request(
            "ID 765-741-434. I want to apply for a solar pump subsidy.",
            provider=_FixedOutputProvider(confidence=True),
        )
        self.assertEqual(result["status"], "FAILED_VALIDATION")

    def test_every_result_includes_steps(self):
        """Early exits still return the step trace for the UI."""
        self.assertIn("steps", process_request(""))


class TestIdentityClaim(TestWorkflowSetup):
    """The workflow only acts on a national ID the citizen supplied."""

    def test_request_without_id_does_not_act_for_anyone(self):
        """Previously this was silently processed as a demo citizen."""
        result = process_request("I want to apply for a solar pump subsidy.")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "NEEDS_NATIONAL_ID")
        self.assertNotIn("transaction", result)
        self.assertIn("national ID", result["message"])

    def test_model_hallucinated_id_is_escalated(self):
        """A model-proposed ID that is not in the request is never used."""
        result = process_request(
            "I am Anjali Iyer, ID 213-163-577. I want a solar pump subsidy.",
            provider=_FixedOutputProvider(national_id="765-741-434"),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "ESCALATED")
        self.assertNotIn("transaction", result)
        self.assertEqual(_last_escalation()["risk_flags"], ["NATIONAL_ID_NOT_GROUNDED"])

    def test_multiple_ids_are_escalated(self):
        """Ambiguous requests naming two citizens go to a human."""
        result = process_request(
            "My ID is 765-741-434 and my neighbour is 213-163-577. Apply for a solar pump subsidy."
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "ESCALATED")
        self.assertNotIn("transaction", result)
        self.assertEqual(_last_escalation()["risk_flags"], ["MULTIPLE_NATIONAL_IDS"])


if __name__ == "__main__":
    unittest.main()
