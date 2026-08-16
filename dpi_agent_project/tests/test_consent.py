"""Tests for DEPA consent engine."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dpi_registries import ensure_database_ready
from depa_consent import (
    validate_consent,
    check_agriculture_consent,
    check_credit_consent,
    _check_status,
    _check_expiry,
    _check_granular_permissions,
    _check_purpose,
    _verify_signature,
)


class TestConsentSetup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_database_ready()


class TestValidConsent(TestConsentSetup):
    """Test consent validation for valid cases."""

    def test_valid_agriculture_consent(self):
        """Citizen 765-741-434 has ACTIVE agri_land consent."""
        result = check_agriculture_consent(
            "765-741-434", "apply_for_solar_pump_subsidy"
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["reason"], "ALL_GATES_PASSED")

    def test_valid_consent_returns_consent_id(self):
        """Valid consent should return a consent_id."""
        result = check_agriculture_consent(
            "765-741-434", "apply_for_solar_pump_subsidy"
        )
        self.assertIsNotNone(result.get("consent_id"))


class TestMissingGranularPermission(TestConsentSetup):
    """Test consent denial for missing granular permissions."""

    def test_credit_denied_agri_only_consent(self):
        """Citizen 213-163-577 has agri_land consent only — credit must fail."""
        result = check_credit_consent(
            "213-163-577", "assess_agricultural_credit"
        )
        self.assertFalse(result["valid"])
        self.assertIn("GRANULAR_PERMISSION_MISSING", result["reason"])


class TestExpiredConsent(TestConsentSetup):
    """Test consent denial for expired records."""

    def test_expired_consent_status(self):
        """Test the expiry gate with an expired timestamp."""
        consent = {
            "consent_status": "ACTIVE",
            "valid_until": "2020-01-01T00:00:00",
            "granular_permissions": "agri_land:read",
            "consent_purpose": "Agricultural Support",
            "consent_id": "TEST",
            "national_id": "TEST",
            "cryptographic_signature": "a" * 64,
        }
        result = _check_expiry(consent)
        self.assertEqual(result, "CONSENT_EXPIRED")


class TestRevokedConsent(TestConsentSetup):
    """Test consent denial for revoked records."""

    def test_revoked_status(self):
        """REVOKED consent must be denied."""
        consent = {"consent_status": "REVOKED"}
        result = _check_status(consent)
        self.assertEqual(result, "CONSENT_REVOKED")

    def test_active_status(self):
        """ACTIVE consent should pass."""
        consent = {"consent_status": "ACTIVE"}
        result = _check_status(consent)
        self.assertIsNone(result)


class TestBadSignature(TestConsentSetup):
    """Test signature verification."""

    def test_missing_signature(self):
        """Missing signature should fail."""
        consent = {"cryptographic_signature": ""}
        result = _verify_signature(consent)
        self.assertEqual(result, "SIGNATURE_MISSING")

    def test_valid_hex_signature(self):
        """Valid 64-char hex signature should pass."""
        consent = {"cryptographic_signature": "a" * 64,
                    "consent_id": "C1", "national_id": "N1",
                    "consent_purpose": "P", "granular_permissions": "GP",
                    "consent_status": "ACTIVE", "valid_until": "2030-01-01"}
        result = _verify_signature(consent)
        self.assertIsNone(result)

    def test_invalid_hex_signature(self):
        """Non-hex signature of 64 chars should fail."""
        consent = {"cryptographic_signature": "z" * 64,
                    "consent_id": "C1", "national_id": "N1",
                    "consent_purpose": "P", "granular_permissions": "GP",
                    "consent_status": "ACTIVE", "valid_until": "2030-01-01"}
        result = _verify_signature(consent)
        self.assertEqual(result, "SIGNATURE_INVALID_FORMAT")


class TestPurposeCheck(TestConsentSetup):
    """Test purpose limitation gate."""

    def test_matching_purpose(self):
        """Consent purpose matches workflow purpose mapping."""
        consent = {"consent_purpose": "Agricultural Support"}
        result = _check_purpose(consent, "apply_for_solar_pump_subsidy")
        self.assertIsNone(result)

    def test_mismatching_purpose(self):
        """Consent purpose does not match workflow."""
        consent = {"consent_purpose": "Unrelated Purpose"}
        result = _check_purpose(consent, "apply_for_solar_pump_subsidy")
        self.assertIn("PURPOSE_MISMATCH", result)


class TestGranularPermissions(TestConsentSetup):
    """Test granular permission gate."""

    def test_has_permission(self):
        """Required permission is in granted set."""
        consent = {"granular_permissions": "agri_land:read,mifos_credit:read"}
        result = _check_granular_permissions(consent, ["agri_land"])
        self.assertIsNone(result)

    def test_missing_permission(self):
        """Required permission is NOT in granted set."""
        consent = {"granular_permissions": "agri_land:read"}
        result = _check_granular_permissions(consent, ["mifos_credit"])
        self.assertIn("GRANULAR_PERMISSION_MISSING", result)

    def test_multiple_permissions_all_present(self):
        """All required permissions are present."""
        consent = {"granular_permissions": "agri_land:read,mifos_credit:read"}
        result = _check_granular_permissions(consent, ["agri_land", "mifos_credit"])
        self.assertIsNone(result)

    def test_multiple_permissions_one_missing(self):
        """One of required permissions is missing."""
        consent = {"granular_permissions": "agri_land:read"}
        result = _check_granular_permissions(consent, ["agri_land", "mifos_credit"])
        self.assertIn("GRANULAR_PERMISSION_MISSING", result)


class TestNoConsentRecords(TestConsentSetup):
    """Test consent for citizen with no records."""

    def test_no_records(self):
        result = validate_consent("000-000-000", ["agri_land"], "apply_for_solar_pump_subsidy")
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "NO_CONSENT_RECORDS")


if __name__ == "__main__":
    unittest.main()
