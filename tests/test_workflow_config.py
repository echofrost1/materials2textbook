from materials2textbook.workflow.config import WorkflowConfig


def test_default_config_allows_pending() -> None:
    config = WorkflowConfig()
    assert config.allows_review_status("Pending_Manual_Timecode")
    assert not config.allows_review_status("rejected")


def test_approved_only_filters_pending() -> None:
    config = WorkflowConfig(include_pending=False)
    assert config.allows_review_status("approved")
    assert not config.allows_review_status("Pending_Manual_Timecode")


def test_allowed_review_statuses_override_pending_policy() -> None:
    config = WorkflowConfig(include_pending=False, allowed_review_statuses={"Pending_Manual_Timecode"})
    assert config.allows_review_status("Pending_Manual_Timecode")
    assert not config.allows_review_status("approved")


def test_include_rejected_can_be_enabled_for_debugging() -> None:
    config = WorkflowConfig(include_rejected=True)
    assert config.allows_review_status("rejected")


def test_review_rounds_are_at_least_one() -> None:
    assert WorkflowConfig(review_rounds=0).normalized_review_rounds() == 1
    assert WorkflowConfig(review_rounds=3).normalized_review_rounds() == 3


def test_token_budget_controls_are_normalized() -> None:
    assert not WorkflowConfig(max_input_tokens=0).token_budget_enabled()
    assert WorkflowConfig(max_input_tokens=100).token_budget_enabled()
    assert WorkflowConfig(max_tokens_per_evidence_chunk=0).normalized_max_tokens_per_evidence_chunk() == 1
    assert WorkflowConfig(summary_token_reserve_ratio=-1).normalized_summary_token_reserve_ratio() == 0.0
    assert WorkflowConfig(summary_token_reserve_ratio=2).normalized_summary_token_reserve_ratio() == 0.8
    assert WorkflowConfig(max_tokens_per_summary_chunk=0).normalized_max_tokens_per_summary_chunk() == 1
    assert WorkflowConfig(max_summary_source_chunks=0).normalized_max_summary_source_chunks() == 1
