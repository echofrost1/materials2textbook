from materials2textbook.workflow.config import WorkflowConfig


def test_default_config_allows_pending() -> None:
    config = WorkflowConfig()
    assert config.allows_review_status("Pending_Manual_Timecode")


def test_approved_only_filters_pending() -> None:
    config = WorkflowConfig(include_pending=False)
    assert config.allows_review_status("approved")
    assert not config.allows_review_status("Pending_Manual_Timecode")


def test_allowed_review_statuses_override_pending_policy() -> None:
    config = WorkflowConfig(include_pending=False, allowed_review_statuses={"Pending_Manual_Timecode"})
    assert config.allows_review_status("Pending_Manual_Timecode")
    assert not config.allows_review_status("approved")
