"""Tests for DPI registry functions."""

import os
import sys
import unittest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dpi_registries import (
    ensure_database_ready,
    verify_identity,
    fetch_land_record,
    fetch_credit_profile,
    get_consent_records,
    get_legacy_transactions,
)


class TestRegistrySetup(unittest.TestCase):
    """Ensure database is initialized before all tests."""

    @classmethod
    def setUpClass(cls):
        ensure_database_ready()


class TestIdentityLookup(TestRegistrySetup):
    """Test verify_identity AI Block."""

    def test_valid_identity(self):
        """Lookup a known ACTIVE citizen."""
        result = verify_identity("765-741-434")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["identity_status"], "ACTIVE")
        self.assertEqual(result["data"]["full_name"], "Suresh Kaur")
        # Phone should be masked
        self.assertTrue(result["data"]["phone_number"].startswith("****"))

    def test_suspended_identity(self):
        """Lookup a SUSPENDED citizen."""
        result = verify_identity("715-454-455")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["identity_status"], "SUSPENDED")

    def test_nonexistent_identity(self):
        """Lookup a citizen that doesn't exist."""
        result = verify_identity("000-000-000")
        self.assertFalse(result["success"])
        self.assertIn("not found", result.get("error", "").lower())

    def test_empty_national_id(self):
        """Empty national_id should fail."""
        result = verify_identity("")
        self.assertFalse(result["success"])

    def test_none_national_id(self):
        """None national_id should fail."""
        result = verify_identity(None)
        self.assertFalse(result["success"])


class TestLandLookup(TestRegistrySetup):
    """Test fetch_land_record AI Block."""

    def test_valid_land_record(self):
        """Lookup a known land record."""
        result = fetch_land_record("765-741-434")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["land_record_id"], "LAND-IN-00007")
        self.assertIsInstance(result["data"]["land_area_acres"], (int, float))

    def test_nonexistent_land(self):
        """Lookup land for nonexistent citizen."""
        result = fetch_land_record("000-000-000")
        self.assertFalse(result["success"])


class TestCreditLookup(TestRegistrySetup):
    """Test fetch_credit_profile AI Block."""

    def test_valid_credit(self):
        """Lookup a known credit profile."""
        result = fetch_credit_profile("765-741-434")
        self.assertTrue(result["success"])
        self.assertIn("credit_score", result["data"])
        self.assertIsInstance(result["data"]["credit_score"], int)

    def test_nonexistent_credit(self):
        """Lookup credit for nonexistent citizen."""
        result = fetch_credit_profile("000-000-000")
        self.assertFalse(result["success"])


class TestConsentRecords(TestRegistrySetup):
    """Test get_consent_records function."""

    def test_valid_consent_records(self):
        """Fetch consent records for a known citizen."""
        result = get_consent_records("765-741-434")
        self.assertTrue(result["success"])
        self.assertIsInstance(result["data"], list)
        self.assertGreater(len(result["data"]), 0)
        # Check structure
        record = result["data"][0]
        self.assertIn("consent_id", record)
        self.assertIn("consent_status", record)

    def test_no_consent_records(self):
        """Fetch consent for nonexistent citizen."""
        result = get_consent_records("000-000-000")
        self.assertFalse(result["success"])


class TestLegacyTransactions(TestRegistrySetup):
    """Test get_legacy_transactions function."""

    def test_valid_transactions(self):
        """Fetch legacy transactions for a known citizen."""
        result = get_legacy_transactions("765-741-434")
        self.assertTrue(result["success"])
        self.assertIsInstance(result["data"], list)


if __name__ == "__main__":
    unittest.main()
