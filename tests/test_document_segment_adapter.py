from materials2textbook.adapters.document_segments import document_segment_to_evidence_chunk


def test_document_segment_to_evidence_chunk() -> None:
    chunk = document_segment_to_evidence_chunk(
        {
            "segment_id": "D1",
            "document_id": "DOC1",
            "document_type": "pdf",
            "document_title": "焊接讲义",
            "heading": "钨极氩弧焊原理",
            "text": "钨极氩弧焊使用惰性气体保护电弧和熔池。",
            "keywords": "钨极氩弧焊;惰性气体",
            "chapter": "钨极氩弧焊基本操作",
            "page": "12",
            "document_path": "docs/demo.pdf",
            "teaching_value": 0.9,
            "review_status": "approved",
        }
    )

    assert chunk.chunk_id == "D1"
    assert chunk.asset_id == "DOC1"
    assert chunk.source_type == "pdf"
    assert chunk.title == "钨极氩弧焊原理"
    assert chunk.locator.page == 12
    assert chunk.score.teaching_value == 0.9
    assert chunk.keywords == ["钨极氩弧焊", "惰性气体"]


def test_ppt_asset_to_evidence_chunk() -> None:
    chunk = document_segment_to_evidence_chunk(
        {
            "ppt_asset_id": "PPT_A000145_S004",
            "source_asset_id": "A000145",
            "source_ppt": "初级标准7.pptx",
            "original_path": "第四周/初级标准7/初级标准7.pptx",
            "slide_index": 4,
            "slide_title": "基本原理",
            "slide_text": "TIG 焊原理图和部件说明。",
            "subject": "焊接技术",
            "material_block": "钨极氩弧焊",
            "material_block_code": "tig_welding",
            "knowledge_point": "基本原理",
            "recommended_chapter": "钨极氩弧焊基本操作",
            "image_paths": "02_working_processing/ppt_images/A000145/slide_004_image_01.jpeg",
            "usefulness_score": 0.8,
            "review_status": "Pending_Agent_Review",
        }
    )

    assert chunk.chunk_id == "PPT_A000145_S004"
    assert chunk.asset_id == "A000145"
    assert chunk.source_type == "ppt_slide"
    assert chunk.locator.page == 4
    assert chunk.locator.keyframe_paths == ["02_working_processing/ppt_images/A000145/slide_004_image_01.jpeg"]
    assert chunk.metadata["source_ppt"] == "初级标准7.pptx"
