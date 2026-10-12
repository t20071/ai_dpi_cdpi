"""Tests for the tamper-evident audit log."""

import json
import threading

from dpi_agent.audit import AuditLog


def _log(tmp_path, n=3):
    log = AuditLog(tmp_path / "test.jsonl")
    for i in range(n):
        log.append({"event": i})
    return log


def _rewrite(log, edit):
    lines = log.path.read_text(encoding="utf-8").splitlines()
    log.path.write_text("\n".join(edit(lines)) + "\n", encoding="utf-8")


def test_records_are_chained(tmp_path):
    log = _log(tmp_path)
    first, second = [json.loads(line) for line in log.path.read_text().splitlines()[:2]]
    assert first["seq"] == 1 and first["prev_hash"] == ""
    assert second["prev_hash"] == first["hash"]
    assert log.verify().ok
    assert log.verify().records == 3


def test_read_returns_newest_first(tmp_path):
    log = _log(tmp_path)
    assert [r["event"] for r in log.read(2)] == [2, 1]


def test_edited_record_is_detected(tmp_path):
    log = _log(tmp_path)

    def edit(lines):
        record = json.loads(lines[1])
        record["event"] = "changed"
        lines[1] = json.dumps(record)
        return lines

    _rewrite(log, edit)
    result = log.verify()
    assert not result.ok
    assert result.line == 2
    assert "modified" in result.error


def test_deleted_record_is_detected(tmp_path):
    log = _log(tmp_path)
    _rewrite(log, lambda lines: [lines[0], lines[2]])
    result = log.verify()
    assert not result.ok
    assert "broken link" in result.error


def test_empty_log_verifies(tmp_path):
    assert AuditLog(tmp_path / "missing.jsonl").verify().ok


def test_concurrent_appends_keep_chain_intact(tmp_path):
    log = AuditLog(tmp_path / "threads.jsonl")
    threads = [
        threading.Thread(target=lambda i=i: [log.append({"t": i, "n": n}) for n in range(20)])
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result = log.verify()
    assert result.ok
    assert result.records == 160
