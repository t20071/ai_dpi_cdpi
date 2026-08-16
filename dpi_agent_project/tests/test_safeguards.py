"""Tests for NIST-inspired safeguards."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nist_safeguards import (
    evaluate_confidence,
    check_ambiguity,
    check_high_impact,
    check_bias_risk,
    check_policy_violations,
    apply_risk_gate,
)


class TestConfidenceEvaluation(unittest.TestCase):
    """Test confidence threshold evaluation."""

    def test_above_threshold(self):
        """Confidence >= 0.80 should pass."""
        result = evaluate_confidence(0.85)
        self.assertTrue(result["passed"])
        self.assertEqual(result["decision"], "PASS")

    def test_at_threshold(self):
        """Confidence == 0.80 should pass."""
        result = evaluate_confidence(0.80)
        self.assertTrue(result["passed"])

    def test_below_threshold(self):
        """Confidence < 0.80 should fail."""
        result = evaluate_confidence(0.75)
        self.assertFalse(result["passed"])
        self.assertEqual(result["decision"], "FAIL_LOW_CONFIDENCE")

    def test_high_impact_uses_higher_threshold(self):
        """High-impact workflows should use 0.90 threshold."""
        result = evaluate_confidence(0.85, "assess_agricultural_credit")
        self.assertFalse(result["passed"])
        self.assertTrue(result["is_high_impact"])

    def test_not_nist_mandated(self):
        """Result should clearly state threshold is not NIST-mandated."""
        result = evaluate_confidence(0.85)
        self.assertIn("NOT prescribed by NIST", result["note"])


class TestAmbiguityCheck(unittest.TestCase):
    """Test ambiguity detection."""

    def test_complete_output_passes(self):
        """Complete SLM output should pass ambiguity check."""
        output = {
            "intent": "apply_for_solar_pump_subsidy",
            "national_id": "765-741-434",
            "requested_action": "assess_and_submit",
            "confidence": 0.91,
            "required_tools": [{"tool": "verify_identity", "arguments": {}}],
        }
        result = check_ambiguity(output)
        self.assertTrue(result["passed"])

    def test_missing_intent_fails(self):
        """Missing intent should fail."""
        output = {
            "national_id": "765-741-434",
            "requested_action": "assess",
            "confidence": 0.9,
            "required_tools": [],
        }
        result = check_ambiguity(output)
        self.assertFalse(result["passed"])
        self.assertTrue(any("intent" in f for f in result["flags"]))

    def test_missing_confidence_fails(self):
        """Missing confidence should fail."""
        output = {
            "intent": "test",
            "national_id": "123",
            "requested_action": "test",
            "required_tools": [{"tool": "t", "arguments": {}}],
        }
        result = check_ambiguity(output)
        self.assertFalse(result["passed"])

    def test_no_tools_flagged(self):
        """No tools requested should be flagged."""
        output = {
            "intent": "test",
            "national_id": "123",
            "requested_action": "test",
            "confidence": 0.9,
            "required_tools": [],
        }
        result = check_ambiguity(output)
        self.assertFalse(result["passed"])


class TestBiasRisk(unittest.TestCase):
    """Test prototype risk-indicator filter."""

    def test_clean_output_passes(self):
        """Clean output should pass."""
        output = {
            "intent": "apply_for_solar_pump_subsidy",
            "confidence": 0.85,
            "required_tools": [{"tool": "verify_identity"}],
        }
        result = check_bias_risk(output)
        self.assertTrue(result["passed"])

    def test_prohibited_terms_detected(self):
        """Prohibited terms should be flagged."""
        output = {
            "intent": "guaranteed approval for all",
            "confidence": 0.9,
            "required_tools": [],
        }
        result = check_bias_risk(output)
        self.assertFalse(result["passed"])
        self.assertTrue(any("PROHIBITED" in f for f in result["flags"]))

    def test_high_confidence_no_tools_flagged(self):
        """Very high confidence with no tools is suspicious."""
        output = {
            "intent": "test",
            "confidence": 0.99,
            "required_tools": [],
        }
        result = check_bias_risk(output)
        self.assertFalse(result["passed"])

    def test_disclaimer_present(self):
        """Result should include prototype disclaimer."""
        output = {"intent": "test", "confidence": 0.5}
        result = check_bias_risk(output)
        self.assertIn("prototype", result["disclaimer"].lower())


class TestHighImpact(unittest.TestCase):
    """Test high-impact classification."""

    def test_credit_is_high_impact(self):
        result = check_high_impact("assess_agricultural_credit")
        self.assertTrue(result["is_high_impact"])

    def test_subsidy_not_high_impact(self):
        result = check_high_impact("apply_for_solar_pump_subsidy")
        self.assertFalse(result["is_high_impact"])


class TestCompositeRiskGate(unittest.TestCase):
    """Test the composite risk gate."""

    def test_good_output_passes(self):
        """Well-formed output with good confidence should pass."""
        output = {
            "intent": "apply_for_solar_pump_subsidy",
            "national_id": "765-741-434",
            "requested_action": "assess_and_submit",
            "confidence": 0.91,
            "required_tools": [
                {"tool": "verify_identity", "arguments": {"national_id": "765-741-434"}},
            ],
            "purpose": "Agricultural Subsidy Assessment",
        }
        result = apply_risk_gate(output)
        self.assertTrue(result["passed"])

    def test_low_confidence_fails(self):
        """Low confidence should fail the gate."""
        output = {
            "intent": "apply_for_solar_pump_subsidy",
            "national_id": "123",
            "requested_action": "assess",
            "confidence": 0.45,
            "required_tools": [
                {"tool": "verify_identity", "arguments": {"national_id": "123"}},
            ],
        }
        result = apply_risk_gate(output)
        self.assertFalse(result["passed"])
        self.assertTrue(any("LOW_CONFIDENCE" in f for f in result.get("all_flags", [])))


class TestPolicyViolations(unittest.TestCase):
    """Test policy violation checks."""

    def test_unsupported_workflow(self):
        """Unsupported workflow intent should be flagged."""
        output = {
            "intent": "hack_the_system",
            "required_tools": [],
        }
        result = check_policy_violations(output)
        self.assertFalse(result["passed"])

    def test_unauthorized_tool(self):
        """Unauthorized tool should be flagged."""
        output = {
            "intent": "apply_for_solar_pump_subsidy",
            "required_tools": [{"tool": "execute_arbitrary_sql"}],
        }
        result = check_policy_violations(output)
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
