"""
Tamper-evident audit logs.

Each log is a JSON Lines file.  Every record carries:

    seq        position in the log, starting at 1
    prev_hash  the ``hash`` of the previous record ("" for the first)
    hash       SHA-256 of the record's canonical JSON (excluding ``hash``)

Editing, deleting or reordering any line breaks the chain, which
``AuditLog.verify()`` (and ``dpi-agent audit verify``) detects.  Like any
unkeyed chain it proves integrity, not authorship: someone who rewrites the
whole file can recompute every hash.  Anchoring the latest hash somewhere
external (a signed timestamp, a ledger) would close that gap.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dpi_agent import config

logger = logging.getLogger(__name__)

_GENESIS = ""


def _digest(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    records: int
    error: str = ""
    line: int | None = None


class AuditLog:
    """Append-only, hash-chained JSONL log.  Safe to share between threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _iter_lines(self) -> Iterator[tuple[int, str]]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for number, line in enumerate(f, start=1):
                if line.strip():
                    yield number, line

    def _tail(self) -> tuple[int, str]:
        """Return (seq, hash) of the last record, or (0, genesis) for an empty log."""
        last = None
        for _, line in self._iter_lines():
            last = line
        if last is None:
            return 0, _GENESIS
        record = json.loads(last)
        return int(record.get("seq", 0)), record.get("hash", _GENESIS)

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Add a record to the chain and return it with seq/prev_hash/hash set."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            seq, prev_hash = self._tail()
            entry = dict(record)
            entry.setdefault("timestamp", datetime.now(UTC).isoformat())
            entry["seq"] = seq + 1
            entry["prev_hash"] = prev_hash
            entry["hash"] = _digest(entry)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            return entry

    def count(self) -> int:
        """Number of records in the log."""
        return sum(1 for _ in self._iter_lines())

    def read(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return up to ``limit`` most recent records, newest first."""
        records = []
        for number, line in self._iter_lines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed line %d in %s", number, self.path)
        return list(reversed(records[-limit:])) if limit > 0 else []

    def verify(self) -> VerifyResult:
        """Check every record's hash and its link to the previous record."""
        prev_hash, count = _GENESIS, 0
        for number, line in self._iter_lines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return VerifyResult(False, count, "malformed JSON", number)
            if record.get("prev_hash") != prev_hash:
                return VerifyResult(False, count, "broken link to previous record", number)
            if record.get("hash") != _digest(record):
                return VerifyResult(False, count, "record contents were modified", number)
            if record.get("seq") != count + 1:
                return VerifyResult(False, count, "sequence gap", number)
            prev_hash, count = record["hash"], count + 1
        return VerifyResult(True, count)


consent_log = AuditLog(config.CONSENT_AUDIT_LOG)
workflow_log = AuditLog(config.WORKFLOW_AUDIT_LOG)
escalation_log = AuditLog(config.ESCALATIONS_LOG)

ALL_LOGS = {
    "consent": consent_log,
    "workflow": workflow_log,
    "escalations": escalation_log,
}
