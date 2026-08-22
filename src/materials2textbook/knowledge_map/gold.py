from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GoldComparison:
    case_id: str
    field: str
    expected: Any
    predicted: Any
    matched: bool


@dataclass
class GoldEvaluation:
    comparisons: list[GoldComparison] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return sum(item.matched for item in self.comparisons)

    @property
    def total(self) -> int:
        return len(self.comparisons)


def load_gold_fixture(path: Path) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return value["cases"]


def evaluate_gold_predictions(cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> GoldEvaluation:
    """Compare explicit expected labels only; absent predictions are failures.

    This is deliberately a small transparent fixture evaluator, rather than a
    statistical benchmark.  It makes identity, role, prerequisite and issue
    mistakes inspectable case by case.
    """
    comparisons: list[GoldComparison] = []
    for case in cases:
        predicted = predictions.get(case["id"], {})
        for field in ("identity", "roles", "prerequisites", "expected_issue"):
            if field in case:
                expected = case[field]
                actual = predicted.get(field)
                comparisons.append(GoldComparison(case["id"], field, expected, actual, actual == expected))
    return GoldEvaluation(comparisons)


def render_gold_evaluation(evaluation: GoldEvaluation) -> str:
    lines = ["# Phase 1.5 Gold Fixture Comparison", "", f"- Exact matches: {evaluation.matched}/{evaluation.total}", "", "## Item comparison", ""]
    for item in evaluation.comparisons:
        status = "PASS" if item.matched else "FAIL"
        lines.append(f"- [{status}] `{item.case_id}` / {item.field}: expected `{item.expected}`, predicted `{item.predicted}`")
    failures = [item for item in evaluation.comparisons if not item.matched]
    lines.extend(["", "## Error cases", ""])
    lines.extend([f"- `{item.case_id}` / {item.field}" for item in failures] or ["- none"])
    return "\n".join(lines).rstrip() + "\n"
