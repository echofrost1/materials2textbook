from materials2textbook.validators.video_segments import validate_video_segments


def valid_record() -> dict:
    return {
        "clip_id": "C1",
        "source_asset_id": "A1",
        "source_video": "demo.mp4",
        "original_path": "raw/demo.mp4",
        "start_time": "00:00:00",
        "end_time": "00:00:30",
        "subject": "焊接技术",
        "material_block": "钨极氩弧焊",
        "material_block_code": "tig_welding",
        "knowledge_point": "送丝",
        "clip_summary": "送丝操作",
        "recommended_chapter": "基本操作",
        "usefulness_score": 0.8,
        "transcript_status": "DONE",
        "evidence_text": "这是一段足够长的证据文本，用于说明送丝操作的关键步骤。",
        "keyframe_paths": "frame.jpg",
        "review_status": "approved",
    }


def test_validate_video_segments_accepts_valid_record() -> None:
    report = validate_video_segments([valid_record()])
    assert report.high_issue_count == 0
    assert report.medium_issue_count == 0


def test_validate_video_segments_detects_duplicate_and_bad_time() -> None:
    first = valid_record()
    second = valid_record() | {"end_time": "00:00:00"}
    report = validate_video_segments([first, second])
    messages = [issue.message for issue in report.issues]
    assert "clip_id 重复。" in messages
    assert "end_time 必须晚于 start_time。" in messages
