"""
Evaluation harness for the interpretation step.

Runs labelled requests (evals/intent_cases.jsonl) through a provider and the
same triage logic the live system uses, then scores two things:

Model quality (what the language model got right on its own)
    schema_valid    reply had every field with the right type
    intent_acc      intent matched the label
    id_acc          national_id was copied exactly from the request

System safety (what the whole pipeline would do)
    outcome_acc     proceed / ask / hold matched the label
    unsafe          would have started a workflow that should not run,
                    or the wrong workflow; this must be 0
    over_held       should have proceeded but was held for a human

Nothing here touches the database or writes audit records: a "proceed"
outcome is recorded, not executed.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dpi_agent.config import EVAL_RESULTS_DIR, REPO_ROOT
from dpi_agent.llm.providers import LLMProvider
from dpi_agent.llm.rules import extract_national_ids
from dpi_agent.workflows.triage import check_identity_claim, triage, validate_plan

DEFAULT_CASES = REPO_ROOT / "evals" / "intent_cases.jsonl"


@dataclass
class CaseResult:
    id: str
    category: str
    expected_intent: str | None
    expected_outcome: str
    outcome: str = ""  # proceed | ask | hold | error
    model_intent: str | None = None
    model_national_id: str | None = None
    schema_valid: bool | None = None
    flags: list[str] | None = None
    latency_ms: float | None = None
    error: str = ""
    id_expected: str | None = None

    @property
    def called_model(self) -> bool:
        return self.schema_valid is not None

    @property
    def unsafe(self) -> bool:
        """Proceeded when it should not have, or with the wrong workflow."""
        return self.outcome == "proceed" and (
            self.expected_outcome != "proceed" or self.model_intent != self.expected_intent
        )


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run_case(provider: LLMProvider, case: dict[str, Any]) -> CaseResult:
    text = case["text"]
    result = CaseResult(
        id=case["id"],
        category=case.get("category", ""),
        expected_intent=case.get("expected_intent"),
        expected_outcome=case["expected_outcome"],
    )
    if check_identity_claim(text) is not None:
        result.outcome = "ask"  # the live system never calls the model here
        return result

    stated = extract_national_ids(text)
    result.id_expected = stated[0] if len(stated) == 1 else None
    try:
        interpretation = provider.interpret(text)
    except Exception as exc:  # noqa: BLE001 - a failed call is a scored result
        result.outcome, result.error, result.schema_valid = "error", str(exc), False
        return result

    plan = interpretation.plan
    result.latency_ms = round(interpretation.latency_ms, 1)
    result.schema_valid = not validate_plan(plan)
    result.model_intent = plan.get("intent") if isinstance(plan, dict) else None
    result.model_national_id = plan.get("national_id") if isinstance(plan, dict) else None

    decision = triage(text, plan)
    result.outcome = "proceed" if decision.action == "proceed" else "hold"
    result.flags = decision.flags or (["INVALID_PLAN"] if decision.action == "invalid" else [])
    return result


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    called = [r for r in results if r.called_model]
    labelled = [r for r in called if r.expected_intent is not None]
    with_id = [r for r in called if r.id_expected is not None]
    latencies = sorted(r.latency_ms for r in called if r.latency_ms is not None)
    return {
        "cases": len(results),
        "model_calls": len(called),
        "errors": sum(r.outcome == "error" for r in results),
        "schema_valid": _rate(sum(bool(r.schema_valid) for r in called), len(called)),
        "intent_acc": _rate(
            sum(r.model_intent == r.expected_intent for r in labelled), len(labelled)
        ),
        "id_acc": _rate(sum(r.model_national_id == r.id_expected for r in with_id), len(with_id)),
        "outcome_acc": _rate(sum(r.outcome == r.expected_outcome for r in results), len(results)),
        "unsafe": sum(r.unsafe for r in results),
        "over_held": sum(r.expected_outcome == "proceed" and r.outcome == "hold" for r in results),
        "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else None,
        "latency_p95_ms": (
            round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))], 1)
            if latencies
            else None
        ),
    }


def evaluate(provider: LLMProvider, cases: list[dict[str, Any]], on_case=None) -> dict[str, Any]:
    """Run every case and return {"provider", "summary", "results"}."""
    results = []
    for case in cases:
        result = run_case(provider, case)
        results.append(result)
        if on_case:
            on_case(result)
    return {
        "provider": provider.name,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "summary": summarize(results),
        "results": [asdict(r) for r in results],
    }


def save_report(report: dict[str, Any], directory: Path = EVAL_RESULTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_name = report["provider"].replace(":", "_").replace("/", "_")
    path = directory / f"{stamp}_{safe_name}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# (summary key, column label, format)
_COLUMNS = [
    ("schema_valid", "schema ok", "{:.0%}"),
    ("intent_acc", "intent acc", "{:.0%}"),
    ("id_acc", "ID acc", "{:.0%}"),
    ("outcome_acc", "outcome acc", "{:.0%}"),
    ("unsafe", "unsafe", "{}"),
    ("over_held", "over-held", "{}"),
    ("errors", "errors", "{}"),
    ("latency_p50_ms", "p50 ms", "{:.0f}"),
    ("latency_p95_ms", "p95 ms", "{:.0f}"),
]


def format_comparison(reports: list[dict[str, Any]]) -> str:
    """A plain-text table with one row per provider."""
    header = ["provider"] + [label for _, label, _ in _COLUMNS]
    rows = [
        [r["provider"]]
        + [
            "-" if r["summary"].get(key) is None else fmt.format(r["summary"][key])
            for key, _, fmt in _COLUMNS
        ]
        for r in reports
    ]
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]

    def line(row: list[str]) -> str:
        return "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True))

    return "\n".join([line(header), line(["-" * w for w in widths]), *map(line, rows)])
