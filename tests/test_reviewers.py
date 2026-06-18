from materials2textbook.agents.reviewers import EvidenceReviewerAgent, PedagogyReviewerAgent
from materials2textbook.schemas import CaseExample, ChapterPlan, EvidenceChunk, EvidenceLocator, EvidenceScore, KnowledgePoint


def make_chunk(chunk_id: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id="A1",
        title="送丝",
        content="送丝操作证据",
        summary="送丝摘要",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
    )


def make_plan() -> ChapterPlan:
    return ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝操作"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", ["C1"])],
        evidence_chunk_ids=["C1"],
    )


def test_evidence_reviewer_detects_missing_draft_citation() -> None:
    issues = EvidenceReviewerAgent().run([make_plan()], [make_chunk("C1")], "## 基本操作\n\n这里没有引用编号。")

    messages = [issue.message for issue in issues["chapter_01"]]
    assert "教材草稿未保留该知识点的证据引用。" in messages


def test_evidence_reviewer_detects_unknown_draft_citation() -> None:
    issues = EvidenceReviewerAgent().run([make_plan()], [make_chunk("C1")], "证据：C999999")

    locations = [issue.location for issue in issues["chapter_01"]]
    assert "C999999" in locations


def test_evidence_reviewer_detects_paragraph_without_support() -> None:
    issues = EvidenceReviewerAgent().run(
        [make_plan()],
        [make_chunk("C1")],
        "送丝操作需要持续保持稳定，但这里没有证据编号。",
    )

    messages = [issue.message for issue in issues["chapter_01"]]
    assert "教材段落缺少可回溯证据引用。" in messages


def test_evidence_reviewer_detects_claim_level_consistency_issue() -> None:
    issues = EvidenceReviewerAgent().run(
        [make_plan()],
        [make_chunk("C1")],
        "送丝操作需要保持稳定，证据：C1。\n\n送丝操作不能保持稳定，证据：C1。",
    )

    messages = [issue.message for issue in issues["chapter_01"]]
    assert any("要求性和禁止性表述冲突" in message for message in messages)


class FakeReviewerLLM:
    def generate(self, messages: list[dict[str, str]]) -> str:
        combined = "\n".join(message["content"] for message in messages)
        if "事实与证据审核" in combined:
            return '[{"severity":"medium","location":"C1","message":"正文断言需要更明确的证据支撑。","suggestion":"补充证据引用或改为待复核表述。"}]'
        return '[{"severity":"low","location":"chapter_01","message":"学习活动缺少难度递进。","suggestion":"按观察、复述、分析三个层级改写活动。"}]'


def test_evidence_reviewer_appends_llm_issues() -> None:
    issues = EvidenceReviewerAgent(llm_provider=FakeReviewerLLM(), use_llm=True).run(
        [make_plan()],
        [make_chunk("C1")],
        "证据：C1",
    )

    assert any(issue.message == "正文断言需要更明确的证据支撑。" for issue in issues["chapter_01"])


def test_pedagogy_reviewer_appends_llm_issues() -> None:
    issues = PedagogyReviewerAgent(llm_provider=FakeReviewerLLM(), use_llm=True).run([make_plan()], "证据：C1")

    assert any(issue.message == "学习活动缺少难度递进。" for issue in issues["chapter_01"])


def test_pedagogy_reviewer_scores_structured_activity_quality() -> None:
    plan = make_plan()
    plan.activity_items = []
    plan.activities = ["观察视频"]

    issues = PedagogyReviewerAgent().run([plan], "证据：C1")
    messages = [issue.message for issue in issues["chapter_01"]]

    assert "章节缺少结构化学习活动。" in messages


def test_pedagogy_reviewer_detects_missing_practice_case_example() -> None:
    plan = make_plan()
    plan.knowledge_points[0].difficulty_level = "practice"
    plan.knowledge_points[0].cluster_id = "operation"

    issues = PedagogyReviewerAgent().run([plan], "证据：C1")
    messages = [issue.message for issue in issues["chapter_01"]]

    assert "章节缺少面向实践或迁移的案例示例。" in messages


def test_pedagogy_reviewer_accepts_grounded_transfer_case_example() -> None:
    plan = make_plan()
    plan.knowledge_points[0].difficulty_level = "practice"
    plan.knowledge_points[0].cluster_id = "operation"
    plan.case_examples = [
        CaseExample(
            case_id="case_01",
            title="送丝迁移案例",
            prompt="课堂实训中，新手学生需要把送丝判断迁移到同类现场任务。",
            reference_answer="参考 C1 说明观察点，并说明同类项目中的迁移判断。",
            target_knowledge_point_ids=["kp_01"],
            evidence_chunk_ids=["C1"],
        )
    ]

    issues = PedagogyReviewerAgent().run([plan], "证据：C1")
    messages = [issue.message for issue in issues["chapter_01"]]

    assert "章节缺少面向实践或迁移的案例示例。" not in messages
    assert "案例示例没有绑定本章证据片段。" not in messages
    assert "案例示例缺少迁移应用或现场判断要求。" not in messages
