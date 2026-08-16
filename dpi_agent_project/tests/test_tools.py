"""Tests for MCP-style tool registry."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dpi_registries import ensure_database_ready
from mcp_tools import (
    execute_tool,
    list_available_tools,
    get_tool_schema,
    _validate_arguments,
    TOOL_SCHEMAS,
)


class TestToolSetup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_database_ready()


class TestToolAllowlist(TestToolSetup):
    """Test tool allowlist enforcement."""

    def test_valid_tool(self):
        """Executing a valid tool should succeed."""
        result = execute_tool("verify_identity", {"national_id": "765-741-434"})
        self.assertTrue(result["success"])
        self.assertEqual(result["tool"], "verify_identity")

    def test_unknown_tool_rejected(self):
        """Unknown tools must be rejected."""
        result = execute_tool("drop_database", {"target": "all"})
        self.assertFalse(result["success"])
        self.assertIn("Unknown or unauthorized", result["error"])

    def test_dangerous_tool_rejected(self):
        """Dangerous tool names must be rejected."""
        result = execute_tool("execute_sql", {"query": "DROP TABLE users"})
        self.assertFalse(result["success"])

    def test_empty_tool_name(self):
        """Empty tool name must be rejected."""
        result = execute_tool("", {})
        self.assertFalse(result["success"])


class TestArgumentValidation(TestToolSetup):
    """Test argument validation."""

    def test_valid_arguments(self):
        """Valid arguments should pass validation."""
        schema = TOOL_SCHEMAS["verify_identity"]
        error = _validate_arguments({"national_id": "123"}, schema)
        self.assertIsNone(error)

    def test_missing_required_argument(self):
        """Missing required argument should fail."""
        schema = TOOL_SCHEMAS["verify_identity"]
        error = _validate_arguments({}, schema)
        self.assertIn("Missing required", error)

    def test_unknown_parameter_rejected(self):
        """Unknown parameters must be rejected."""
        schema = TOOL_SCHEMAS["verify_identity"]
        error = _validate_arguments({"national_id": "123", "admin": True}, schema)
        self.assertIn("Unknown parameter", error)

    def test_wrong_type(self):
        """Wrong parameter type should fail."""
        schema = TOOL_SCHEMAS["verify_identity"]
        error = _validate_arguments({"national_id": 123}, schema)
        self.assertIn("wrong type", error)

    def test_malformed_arguments(self):
        """Non-dict arguments should fail."""
        result = execute_tool("verify_identity", "not a dict")
        self.assertFalse(result["success"])
        self.assertIn("must be a JSON object", result["error"])


class TestToolExecution(TestToolSetup):
    """Test actual tool execution."""

    def test_verify_identity_execution(self):
        """verify_identity should return identity data."""
        result = execute_tool("verify_identity", {"national_id": "765-741-434"})
        self.assertTrue(result["success"])
        inner = result["result"]
        self.assertTrue(inner["success"])
        self.assertEqual(inner["data"]["full_name"], "Suresh Kaur")

    def test_fetch_land_record_execution(self):
        """fetch_land_record should return land data."""
        result = execute_tool("fetch_land_record", {"national_id": "765-741-434"})
        self.assertTrue(result["success"])
        inner = result["result"]
        self.assertTrue(inner["success"])
        self.assertIn("land_area_acres", inner["data"])

    def test_assess_credit_profile_execution(self):
        """assess_credit_profile should return credit data."""
        result = execute_tool("assess_credit_profile", {"national_id": "765-741-434"})
        self.assertTrue(result["success"])
        inner = result["result"]
        self.assertTrue(inner["success"])
        self.assertIn("credit_score", inner["data"])


class TestToolListing(TestToolSetup):
    """Test tool registry introspection."""

    def test_list_tools(self):
        """Should list all available tools."""
        tools = list_available_tools()
        self.assertIsInstance(tools, list)
        self.assertGreater(len(tools), 0)
        names = [t["name"] for t in tools]
        self.assertIn("verify_identity", names)
        self.assertIn("fetch_land_record", names)

    def test_get_schema(self):
        """Should return schema for valid tool."""
        schema = get_tool_schema("verify_identity")
        self.assertIsNotNone(schema)
        self.assertEqual(schema["name"], "verify_identity")

    def test_get_schema_unknown(self):
        """Should return None for unknown tool."""
        schema = get_tool_schema("nonexistent")
        self.assertIsNone(schema)


if __name__ == "__main__":
    unittest.main()
