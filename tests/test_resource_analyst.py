from materials2textbook.agents.resource_analyst import ResourceAnalystAgent
from materials2textbook.prompts.resource_analyst import build_resource_analyst_messages
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


class FakeLLM:
    def generate(self, messages: list[dict[str, str]]) -> str:
        assert "不得新增片段之外的事实" in messages[0]["content"]
        return """
        {
          "summary": "送丝操作需要稳定观察熔池。",
          "keywords": ["送丝", "熔池", "操作"],
          "normalized_content": "送丝时需要观察熔池并保持动作稳定。",
          "quality_notes": "ASR 可读，仍需人工确认时间码。",
          "uncertain": true
        }
        """


def make_record() -> dict[str, str]:
    return {
        "clip_id": "C1",
        "source_asset_id": "A1",
        "source_video": "demo.mp4",
        "knowledge_point": "送丝",
        "clip_summary": "原摘要",
        "evidence_text": "送丝的时候要看熔池，动作稳定。",
        "tags": "送丝;操作",
        "recommended_chapter": "基本操作",
        "start_time": "00:00:01",
        "end_time": "00:00:03",
        "review_status": "Pending_Manual_Timecode",
    }


def test_resource_analyst_llm_enhances_chunk_without_losing_traceability() -> None:
    agent = ResourceAnalystAgent(llm_provider=FakeLLM(), use_llm=True)
    chunk = agent.run([make_record()])[0]

    assert chunk.chunk_id == "C1"
    assert chunk.summary == "送丝操作需要稳定观察熔池。"
    assert chunk.keywords == ["送丝", "熔池", "操作"]
    assert chunk.content == "送丝时需要观察熔池并保持动作稳定。"
    assert chunk.metadata["raw_evidence_text"] == "送丝的时候要看熔池，动作稳定。"
    assert chunk.metadata["llm_resource_analysis"]["uncertain"] is True


def test_resource_analyst_prompt_demands_json_and_no_extra_facts() -> None:
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="送丝",
        content="送丝证据文本",
        summary="送丝摘要",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(),
        review_status="Pending_Manual_Timecode",
    )

    messages = build_resource_analyst_messages(chunk)
    combined = "\n".join(message["content"] for message in messages)
    assert "只输出 JSON 对象" in combined
    assert "不得新增片段之外的事实" in combined
    assert "normalized_content" in combined


def test_resource_analyst_accepts_document_segments() -> None:
    agent = ResourceAnalystAgent()
    chunks = agent.run_document_segments(
        [
            {
                "segment_id": "D1",
                "document_id": "DOC1",
                "heading": "焊接安全",
                "text": "焊接前应检查设备和防护用品。",
                "chapter": "焊接准备",
                "review_status": "approved",
            }
        ]
    )

    assert len(chunks) == 1
    assert chunks[0].chunk_id == "D1"
    assert chunks[0].source_type == "document_segment"
