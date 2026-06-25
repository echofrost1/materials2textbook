from __future__ import annotations

from pathlib import Path

from materials2textbook.agents.book_plan_llm import BookPlanLLMAgent, book_plan_from_llm_payload, plan_has_blocking_issues
from materials2textbook.agents.domain_config_agent import DomainConfigAgent
from materials2textbook.domain_config import DomainConfig
from materials2textbook.prompts.ability_graph import build_ability_graph_messages
from materials2textbook.prompts.exercises import build_exercises_messages
from materials2textbook.prompts.textbook_writer import build_textbook_writer_messages
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, EvidenceLocator, EvidenceScore, KnowledgePoint


class StaticLLM:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, messages: list[dict[str, str]]) -> str:
        return self.response


def chunk(chunk_id: str, title: str, chapter: str = "maintenance") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id=chunk_id,
        title=title,
        content=f"{title} evidence for diagnostics and repair.",
        summary=f"{title} summary",
        keywords=[title],
        subject="automotive repair",
        material_block=chapter,
        material_block_code=chapter,
        recommended_chapter=chapter,
        locator=EvidenceLocator(path=f"{chunk_id}.md"),
        score=EvidenceScore(teaching_value=0.8),
        source_type="reference_text",
        review_status="approved",
    )


def test_domain_config_agent_uses_fake_llm(tmp_path: Path) -> None:
    json_dir = tmp_path / "02_working_processing" / "json"
    json_dir.mkdir(parents=True)
    (json_dir / "reference_text_assets.jsonl").write_text(
        '{"chunk_id":"R1","title":"brake inspection","content":"disc brake pad inspection"}\n',
        encoding="utf-8",
    )
    agent = DomainConfigAgent(
        llm_provider=StaticLLM(
            '{"domain_name":"automotive repair","audience":"vocational learners","textbook_type":"digital textbook",'
            '"domain_terms":["brake","engine"],"operation_terms":["inspect","diagnose"],'
            '"quality_dimensions":["safety","accuracy"],"observation_examples":["wear pattern"]}'
        ),
        use_llm=True,
    )

    config = agent.run(title="Auto Repair", material_root=tmp_path)

    assert config.domain_name == "automotive repair"
    assert "brake" in config.domain_terms
    assert "inspect" in config.operation_terms


def test_book_plan_llm_repairs_short_chapter_sections() -> None:
    chunks = [chunk(f"C{i}", f"topic {i}", "diagnostics") for i in range(1, 6)]
    payload = {
        "title": "Auto Repair",
        "chapters": [
            {
                "chapter_no": 1,
                "title": "Diagnostics",
                "learning_goals": ["diagnose common faults"],
                "sections": [{"section_no": "1.1", "title": "Fault scan", "knowledge_points": ["scan"], "primary_material_ids": ["C1"]}],
            },
            {
                "chapter_no": 2,
                "title": "Brake Service",
                "sections": [{"section_no": "2.1", "title": "Pad check", "knowledge_points": ["pads"], "primary_material_ids": ["C2"]}],
            },
            {
                "chapter_no": 3,
                "title": "Engine Service",
                "sections": [{"section_no": "3.1", "title": "Oil check", "knowledge_points": ["oil"], "primary_material_ids": ["C3"]}],
            },
        ],
    }

    plan, issues = book_plan_from_llm_payload(
        payload,
        title="Auto Repair",
        chunks=chunks,
        domain_config=DomainConfig(domain_name="automotive repair"),
    )

    assert not plan_has_blocking_issues(issues)
    assert len(plan.chapters) == 3
    assert all(len(chapter.sections) >= 3 for chapter in plan.chapters)


def test_book_plan_llm_blocks_missing_chapters() -> None:
    plan, issues = book_plan_from_llm_payload(
        {"title": "Auto Repair", "chapters": [{"title": "Only one", "sections": []}]},
        title="Auto Repair",
        chunks=[chunk("C1", "topic")],
        domain_config=DomainConfig(domain_name="automotive repair"),
    )

    assert plan.title == "Auto Repair"
    assert plan_has_blocking_issues(issues)


def test_domain_prompts_do_not_contain_welding_examples() -> None:
    config = DomainConfig(
        domain_name="automotive repair",
        domain_terms=["brake", "engine", "diagnostic scanner"],
        operation_terms=["inspect", "diagnose", "replace"],
    )
    c = chunk("C1", "brake inspection", "brake system")
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="Brake System",
        learning_goals=["Inspect brake wear"],
        knowledge_points=[KnowledgePoint("kp_01", "brake inspection", ["C1"])],
        evidence_chunk_ids=["C1"],
    )

    prompts = []
    prompts.extend(build_textbook_writer_messages([plan], [c], "Auto Repair", domain_config=config))
    prompts.extend(
        build_ability_graph_messages(
            project_title="Brake System",
            learning_goals=["Inspect brake wear"],
            tasks=[],
            fallback_graph={},
            domain_config=config,
        )
    )
    prompts.extend(
        build_exercises_messages(
            task_title="Brake inspection",
            knowledge_points=[{"title": "brake inspection"}],
            evidence_summary="Brake pad thickness must be inspected.",
            domain_config=config,
        )
    )
    combined = "\n".join(message["content"] for message in prompts)

    for forbidden in ["TIG", "welding", "arc", "wire feeding", "molten pool"]:
        assert forbidden.lower() not in combined.lower()
    assert "automotive repair" in combined
    assert "brake" in combined
