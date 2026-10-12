"""Tests for DPI registry functions."""

import unittest

# Ensure project root is on path
from dpi_agent.registries.database import (
    ensure_database_ready,
    fetch_credit_profile,
    fetch_land_record,
    get_consent_records,
    get_legacy_transactions,
    mask_phone,
    verify_identity,
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


class TestMaskPhone(unittest.TestCase):
    """Test phone number masking."""

    def test_plain_number_shows_last_four(self):
        self.assertEqual(mask_phone("919876543210"), "****3210")

    def test_formatted_number_shows_last_four(self):
        self.assertEqual(mask_phone("+91 98765 43210"), "****3210")

    def test_scientific_notation_fully_masked(self):
        """Excel-mangled values have lost their real trailing digits."""
        self.assertEqual(mask_phone("9.18171E+11"), "****")

    def test_empty_and_short_values(self):
        self.assertEqual(mask_phone(""), "****")
        self.assertEqual(mask_phone(None), "****")
        self.assertEqual(mask_phone("123"), "****")


if __name__ == "__main__":
    unittest.main()
