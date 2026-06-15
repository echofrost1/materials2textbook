from materials2textbook.adapters.video_segments import parse_time_to_ms, video_segment_to_evidence_chunk


def test_parse_time_to_ms() -> None:
    assert parse_time_to_ms("00:01:02") == 62000
    assert parse_time_to_ms("01:02:03.400") == 3723400


def test_video_segment_to_evidence_chunk() -> None:
    chunk = video_segment_to_evidence_chunk(
        {
            "clip_id": "C1",
            "source_asset_id": "A1",
            "knowledge_point": "送丝",
            "evidence_text": "示例证据",
            "tags": "TIG;操作",
            "recommended_chapter": "基本操作",
            "start_time": "00:00:01",
            "end_time": "00:00:03",
        }
    )
    assert chunk.chunk_id == "C1"
    assert chunk.title == "送丝"
    assert chunk.locator.start_ms == 1000
    assert chunk.keywords == ["TIG", "操作"]
