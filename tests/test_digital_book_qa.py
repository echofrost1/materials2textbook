from materials2textbook.digital_book_qa import (
    answer_digital_book_payload,
    answer_digital_book_question,
    build_book_qa_messages,
    render_book_answer_markdown,
    search_digital_book,
)


def sample_book() -> dict:
    return {
        "book_id": "tig-book",
        "title": "样章",
        "projects": [
            {
                "project_id": "p1",
                "title": "项目1 钨极氩弧焊基本操作",
                "tasks": [
                    {
                        "task_id": "t1",
                        "title": "任务1.1 钨极氩弧焊基本操作",
                        "blocks": [
                            {
                                "block_id": "b1",
                                "type": "content",
                                "title": "送丝操作要点",
                                "markdown": "送丝操作需要观察熔池状态，并保持送丝稳定。",
                                "items": [],
                                "evidence_chunk_ids": ["C000010"],
                            },
                            {
                                "block_id": "b2",
                                "type": "case_example",
                                "title": "收弧操作证据分析示例",
                                "markdown": "收弧操作需要结合 C000012 判断。",
                                "items": [],
                                "evidence_chunk_ids": ["C000012"],
                            },
                        ],
                    }
                ],
            }
        ],
    }


def test_search_digital_book_scores_query_and_evidence_id() -> None:
    results = search_digital_book(sample_book(), "C000010 送丝", limit=2)

    assert results[0].block_id == "b1"
    assert results[0].evidence_chunk_ids == ["C000010"]


def test_answer_digital_book_question_without_llm_keeps_sources() -> None:
    answer = answer_digital_book_question(sample_book(), "送丝怎么观察")

    assert not answer.used_llm
    assert "送丝操作要点" in answer.answer
    assert "C000010" in answer.answer
    assert answer.sources[0].block_id == "b1"


class FakeLLM:
    def generate(self, messages: list[dict[str, str]]) -> str:
        combined = "\n".join(message["content"] for message in messages)
        assert "不得补充片段之外的事实" in combined
        assert "C000010" in combined
        return "应结合教材片段说明送丝观察点。证据：C000010"


def test_answer_digital_book_question_with_llm() -> None:
    answer = answer_digital_book_question(sample_book(), "送丝观察", llm_provider=FakeLLM())

    assert answer.used_llm
    assert answer.answer == "应结合教材片段说明送丝观察点。证据：C000010"


def test_render_book_answer_markdown_contains_sources() -> None:
    answer = answer_digital_book_question(sample_book(), "收弧")
    markdown = render_book_answer_markdown(answer)

    assert "# 问书结果" in markdown
    assert "收弧操作证据分析示例" in markdown
    assert "C000012" in markdown


def test_build_book_qa_messages_requires_evidence_bound_answer() -> None:
    results = search_digital_book(sample_book(), "送丝")
    messages = build_book_qa_messages("送丝", results)

    assert "只能依据给定教材片段回答" in messages[0]["content"]
    assert "evidence_chunk_ids" in messages[1]["content"]


def test_answer_payload_contract_for_frontend_endpoint() -> None:
    payload = {
        "question": "wire feed",
        "sources": [
            {
                "block_id": "b1",
                "project_title": "Project 1",
                "task_title": "Task 1",
                "block_title": "Wire feed",
                "text": "Keep wire feed stable and observe the molten pool.",
                "evidence_chunk_ids": ["C1", "C1"],
                "score": 12,
            }
        ],
    }

    answer = answer_digital_book_payload(payload)

    assert answer["used_llm"] is False
    assert answer["citations"] == ["C1"]
    assert "Wire feed" in answer["answer"]
