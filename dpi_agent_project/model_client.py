"""
Sovereign DPI-AI Public Agent — Pluggable LLM Client

Provides a provider-abstraction layer so the system can switch between:
    - OllamaProvider   (default) — local SLM via Ollama
    - CloudProvider     (stub)   — placeholder for OpenAI / Anthropic / etc.
    - DeterministicProvider       — offline testing with pre-built responses

Switch providers by setting LLM_PROVIDER in .env or environment:
    LLM_PROVIDER=ollama          (default)
    LLM_PROVIDER=cloud           (not yet implemented)
    LLM_PROVIDER=deterministic   (for testing without Ollama)

The SLM output contract is enforced: the model returns ONLY structured JSON
with intent, language, national_id, requested_action, required_tools,
purpose, and confidence.  The SLM must NEVER return executable Python, SQL,
shell commands, or arbitrary tool invocations.
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from config import OLLAMA_HOST, OLLAMA_MODEL, LLM_PROVIDER, SCENARIO_CITIZENS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SLM system prompt — constrains output to structured JSON only
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a DPI Public Agent assistant. You help citizens access government services.

You MUST respond ONLY with a valid JSON object. Do NOT include any text, explanation, markdown, or code outside the JSON.

The JSON object MUST have this exact structure:
{
  "intent": "<workflow_name>",
  "language": "<detected_language_code>",
  "national_id": "<citizen_id_if_mentioned>",
  "requested_action": "<action_description>",
  "required_tools": [
    {
      "tool": "<tool_name>",
      "arguments": {
        "<param>": "<value>"
      }
    }
  ],
  "purpose": "<purpose_description>",
  "confidence": <float_between_0_and_1>
}

Available workflow intents:
- apply_for_solar_pump_subsidy
- assess_agricultural_credit

Available tools:
- verify_identity (arguments: national_id)
- fetch_land_record (arguments: national_id)
- assess_credit_profile (arguments: national_id)
- check_consent (arguments: national_id, required_permissions, workflow_purpose)

Rules:
1. Extract the national_id from the user's message if present
2. Determine the most appropriate workflow intent
3. List the tools needed in execution order
4. Set confidence based on how clear the request is (0.0 to 1.0)
5. NEVER include executable code, SQL, shell commands, or Python
6. ALWAYS respond with valid JSON only
"""


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate a text response from the model."""
        ...

    @abstractmethod
    def generate_json(self, prompt: str, system_prompt: str = "") -> Dict[str, Any]:
        """Generate a structured JSON response from the model."""
        ...

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Check if the LLM service is available."""
        ...


# ---------------------------------------------------------------------------
# Ollama Provider (default)
# ---------------------------------------------------------------------------

class OllamaProvider(LLMProvider):
    """Local SLM via Ollama HTTP API."""

    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL):
        self.host = host.rstrip("/")
        self.model = model
        self.generate_url = f"{self.host}/api/generate"

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate a text response from Ollama."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system_prompt or SYSTEM_PROMPT,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 1024,
            },
        }

        try:
            req = Request(
                self.generate_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("response", "")
        except (URLError, HTTPError) as e:
            logger.error("Ollama generate failed: %s", str(e))
            raise ConnectionError(f"Ollama connection failed: {e}") from e

    def generate_json(self, prompt: str, system_prompt: str = "") -> Dict[str, Any]:
        """Generate and parse a structured JSON response from Ollama."""
        raw = self.generate(prompt, system_prompt or SYSTEM_PROMPT)

        # Try to extract JSON from response
        parsed = _extract_json(raw)
        if parsed is not None:
            return parsed

        logger.warning("Failed to parse JSON from SLM response: %s", raw[:200])
        raise ValueError(f"SLM did not return valid JSON. Raw response: {raw[:500]}")

    def health_check(self) -> Dict[str, Any]:
        """Check Ollama service availability."""
        try:
            req = Request(f"{self.host}/api/tags", method="GET")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                model_available = any(self.model in m for m in models)
                return {
                    "available": True,
                    "host": self.host,
                    "model": self.model,
                    "model_loaded": model_available,
                    "models": models,
                }
        except (URLError, HTTPError) as e:
            return {
                "available": False,
                "host": self.host,
                "model": self.model,
                "error": str(e),
            }


# ---------------------------------------------------------------------------
# Cloud Provider (stub)
# ---------------------------------------------------------------------------

class CloudProvider(LLMProvider):
    """
    Placeholder for cloud LLM providers (OpenAI, Anthropic, Google, etc.).

    To implement:
    1. Set CLOUD_LLM_API_KEY, CLOUD_LLM_MODEL, CLOUD_LLM_ENDPOINT in .env
    2. Replace the NotImplementedError calls with actual API calls
    3. Ensure the same structured JSON output contract is enforced
    """

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        raise NotImplementedError(
            "Cloud LLM provider is a stub. "
            "Implement API calls to your preferred cloud LLM service. "
            "Set LLM_PROVIDER=ollama to use local Ollama instead."
        )

    def generate_json(self, prompt: str, system_prompt: str = "") -> Dict[str, Any]:
        raise NotImplementedError(
            "Cloud LLM provider is a stub. "
            "See CloudProvider.generate() for implementation instructions."
        )

    def health_check(self) -> Dict[str, Any]:
        return {
            "available": False,
            "provider": "cloud",
            "error": "Cloud provider not yet implemented. Set LLM_PROVIDER=ollama.",
        }


# ---------------------------------------------------------------------------
# Deterministic Provider (for offline testing)
# ---------------------------------------------------------------------------

class DeterministicProvider(LLMProvider):
    """
    Deterministic provider for offline testing without Ollama.

    Returns pre-built structured responses based on keyword matching.
    Useful for running the three demo scenarios and unit tests.
    """

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        result = self.generate_json(prompt, system_prompt)
        return json.dumps(result, indent=2)

    def generate_json(self, prompt: str, system_prompt: str = "") -> Dict[str, Any]:
        prompt_lower = prompt.lower()

        # Extract national ID from prompt
        national_id = _extract_national_id(prompt)

        # Scenario A: Solar pump subsidy
        if any(kw in prompt_lower for kw in ["solar", "subsidy", "pump", "solar pump"]):
            return {
                "intent": "apply_for_solar_pump_subsidy",
                "language": "en",
                "national_id": national_id or SCENARIO_CITIZENS["scenario_a"]["national_id"],
                "requested_action": "assess_and_submit",
                "required_tools": [
                    {"tool": "verify_identity", "arguments": {"national_id": national_id or SCENARIO_CITIZENS["scenario_a"]["national_id"]}},
                    {"tool": "fetch_land_record", "arguments": {"national_id": national_id or SCENARIO_CITIZENS["scenario_a"]["national_id"]}},
                ],
                "purpose": "Agricultural Subsidy Assessment",
                "confidence": 0.91,
            }

        # Scenario B: Credit / loan request
        if any(kw in prompt_lower for kw in ["credit", "loan", "financial", "mifos"]):
            return {
                "intent": "assess_agricultural_credit",
                "language": "en",
                "national_id": national_id or SCENARIO_CITIZENS["scenario_b"]["national_id"],
                "requested_action": "assess_credit",
                "required_tools": [
                    {"tool": "verify_identity", "arguments": {"national_id": national_id or SCENARIO_CITIZENS["scenario_b"]["national_id"]}},
                    {"tool": "assess_credit_profile", "arguments": {"national_id": national_id or SCENARIO_CITIZENS["scenario_b"]["national_id"]}},
                ],
                "purpose": "Credit Assessment",
                "confidence": 0.88,
            }

        # Scenario C: Ambiguous / escalation
        if any(kw in prompt_lower for kw in ["help", "confused", "not sure", "problem", "issue"]):
            return {
                "intent": "apply_for_solar_pump_subsidy",
                "language": "en",
                "national_id": national_id or SCENARIO_CITIZENS["scenario_c"]["national_id"],
                "requested_action": "assess_and_submit",
                "required_tools": [
                    {"tool": "verify_identity", "arguments": {"national_id": national_id or SCENARIO_CITIZENS["scenario_c"]["national_id"]}},
                ],
                "purpose": "General Assistance",
                "confidence": 0.45,
            }

        # Default: attempt subsidy with low confidence
        return {
            "intent": "apply_for_solar_pump_subsidy",
            "language": "en",
            "national_id": national_id or "",
            "requested_action": "assess_and_submit",
            "required_tools": [
                {"tool": "verify_identity", "arguments": {"national_id": national_id or "UNKNOWN"}},
            ],
            "purpose": "General DPI Service Request",
            "confidence": 0.65,
        }

    def health_check(self) -> Dict[str, Any]:
        return {
            "available": True,
            "provider": "deterministic",
            "note": "Offline deterministic provider for testing. No LLM required.",
        }


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Try to extract a JSON object from text that may contain extra content."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON block in markdown code fence
    patterns = [
        r'```json\s*(.*?)\s*```',
        r'```\s*(.*?)\s*```',
        r'\{.*\}',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                candidate = match.group(1) if match.lastindex else match.group(0)
                return json.loads(candidate)
            except (json.JSONDecodeError, IndexError):
                continue

    return None


def _extract_national_id(text: str) -> Optional[str]:
    """Try to extract a national ID pattern (XXX-XXX-XXX) from text."""
    match = re.search(r'\b(\d{3}-\d{3}-\d{3})\b', text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def get_provider(provider_name: Optional[str] = None) -> LLMProvider:
    """
    Get the configured LLM provider instance.

    Args:
        provider_name: Override provider name. If None, uses LLM_PROVIDER config.

    Returns:
        LLMProvider instance
    """
    name = (provider_name or LLM_PROVIDER).lower().strip()

    if name == "ollama":
        return OllamaProvider()
    elif name == "cloud":
        return CloudProvider()
    elif name == "deterministic":
        return DeterministicProvider()
    else:
        logger.warning("Unknown provider '%s', falling back to deterministic.", name)
        return DeterministicProvider()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

# Default provider instance
_default_provider: Optional[LLMProvider] = None


def get_default_provider() -> LLMProvider:
    """Get or create the default provider instance."""
    global _default_provider
    if _default_provider is None:
        _default_provider = get_provider()
    return _default_provider


def set_default_provider(provider: LLMProvider) -> None:
    """Override the default provider (useful for testing)."""
    global _default_provider
    _default_provider = provider


def generate(prompt: str, system_prompt: str = "") -> str:
    """Generate text using the default provider."""
    return get_default_provider().generate(prompt, system_prompt)


def generate_json(prompt: str, system_prompt: str = "") -> Dict[str, Any]:
    """Generate structured JSON using the default provider."""
    return get_default_provider().generate_json(prompt, system_prompt)


def health_check() -> Dict[str, Any]:
    """Check the default provider's health."""
    return get_default_provider().health_check()
