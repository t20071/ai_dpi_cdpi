"""
Deterministic text rules.

These rules do two jobs:

1. They are the offline interpreter (``DeterministicProvider``), so the whole
   system runs without a language model.
2. They are an independent check on the model.  The workflow only runs when
   the model's intent matches what these rules see in the request (the
   "two-key" rule in ``workflows/triage.py``).

Keywords cover English plus common Hindi / Hinglish terms.
"""

from __future__ import annotations

import re

from dpi_agent.config import CREDIT_WORKFLOW, SUBSIDY_WORKFLOW

_NATIONAL_ID = re.compile(r"\b(\d{3}-\d{3}-\d{3})\b")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Latin-script keywords are matched as whole words (plural "s" allowed).
# Devanagari keywords are matched as substrings, because Python's \b does not
# treat Devanagari vowel signs as word characters.
_INTENT_KEYWORDS: dict[str, tuple[list[str], list[str]]] = {
    SUBSIDY_WORKFLOW: (
        ["solar", "subsidy", "subsidies", "pump", "irrigation", "anudan", "sinchai"],
        ["सोलर", "पंप", "सब्सिडी", "अनुदान", "सिंचाई"],
    ),
    CREDIT_WORKFLOW: (
        ["credit", "loan", "kcc", "borrow", "mifos", "karz", "karza", "karj"],
        ["लोन", "ऋण", "कर्ज", "क्रेडिट"],
    ),
}

_INTENT_PATTERNS = {
    intent: (
        re.compile(r"\b(" + "|".join(map(re.escape, latin)) + r")s?\b", re.IGNORECASE),
        devanagari,
    )
    for intent, (latin, devanagari) in _INTENT_KEYWORDS.items()
}


def extract_national_ids(text: str) -> list[str]:
    """Return the distinct national IDs (XXX-XXX-XXX) in text, in order of appearance."""
    return list(dict.fromkeys(_NATIONAL_ID.findall(text or "")))


def matched_intents(text: str) -> list[str]:
    """Return every workflow whose keywords appear in the text (subsidy first)."""
    found = []
    for intent, (latin, devanagari) in _INTENT_PATTERNS.items():
        if latin.search(text or "") or any(word in (text or "") for word in devanagari):
            found.append(intent)
    return found


def detect_language(text: str) -> str:
    """Very small language guess: Hindi if the text uses Devanagari, else English."""
    return "hi" if _DEVANAGARI.search(text or "") else "en"
