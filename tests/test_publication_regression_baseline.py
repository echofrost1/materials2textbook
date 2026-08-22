from __future__ import annotations

import json
from pathlib import Path


def test_phase3e_passing_baseline_encodes_non_negotiable_release_invariants() -> None:
    baseline_path = Path(__file__).parent / "fixtures" / "publication_regression" / "phase3e_passing_baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    invariants = baseline["invariants"]
    assert invariants["semantic_closed_loop_status"] == "PASS"
    assert invariants["publication_quality_status"] == "PASS"
    assert invariants["final_publication_status"] == "PASS"
    assert invariants["publication_gate_publishable"] is True
    assert invariants["blocker_count"] == 0
    assert invariants["high_count"] == 0
    assert invariants["fallback_count"] == 0
    assert invariants["markdown_digital_alignment"] == 1.0
    assert invariants["outline_signature_unchanged"] is True
    assert invariants["source_book_plan_unchanged"] is True
    assert invariants["semantic_objects_unchanged"] is True
    assert invariants["no_accepted_partial"] is True
    assert invariants["no_silent_fallback"] is True
    assert invariants["unresolved_high_severity_issues"] == 0
