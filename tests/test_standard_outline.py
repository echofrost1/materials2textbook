import json

from materials2textbook.exporters.digital_book import export_digital_book
from materials2textbook.outline import generate_standard_outline, validate_outline
from materials2textbook.schemas import ChapterPlan, KnowledgePoint


def _book() -> dict:
    return {
        "title": "示例教材",
        "projects": [
            {
                "project_id": "project_1",
                "title": "基础项目",
                "tasks": [
                    {
                        "task_id": "task_1",
                        "title": "任务一",
                        "blocks": [
                            {"block_id": "block_1", "title": "对象认知"},
                            {"block_id": "block_2", "title": "流程认识"},
                        ],
                    }
                ],
            }
        ],
    }


def test_standard_outline_numbers_follow_canonical_order() -> None:
    payload = generate_standard_outline(_book()).to_dict()
    project = payload["projects"][0]
    task = project["tasks"][0]
    assert project["display_number"] == "项目一"
    assert task["display_number"] == "任务1-1"
    assert task["learning_units"][1]["display_number"] == "学习单元1-1-2"
    assert task["learning_units"][1]["source_node_id"] == "block_2"


def test_standard_outline_validator_accepts_canonical_outline() -> None:
    outline = generate_standard_outline(_book())
    report = validate_outline(outline, _book())
    assert report.passed
    assert report.preview_allowed
    assert report.release_allowed


def test_standard_outline_validator_rejects_numbering_mutation() -> None:
    outline = generate_standard_outline(_book()).to_dict()
    outline["projects"][0]["tasks"][0]["learning_units"][1]["number_path"] = [1, 1, 3]
    outline["projects"][0]["tasks"][0]["learning_units"][1]["display_number"] = "学习单元1-1-3"
    report = validate_outline(outline, _book())
    assert not report.preview_allowed
    assert {issue.code for issue in report.errors} >= {
        "number_path_not_continuous",
        "display_number_mismatch",
    }


def test_standard_outline_validator_rejects_title_mutation() -> None:
    outline = generate_standard_outline(_book()).to_dict()
    outline["projects"][0]["tasks"][0]["learning_units"][0]["title"] = "外部改写标题"
    report = validate_outline(outline, _book())
    assert not report.preview_allowed
    assert any(issue.code == "title_mismatch" for issue in report.errors)


def test_exporter_persists_standard_outline_and_validation(tmp_path) -> None:
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基础项目",
        learning_goals=["理解基本对象"],
        knowledge_points=[KnowledgePoint("kp_01", "对象认知", [])],
        evidence_chunk_ids=[],
    )
    _, json_path, _ = export_digital_book(
        title="示例教材",
        plans=[plan],
        chunks=[],
        output_dir=tmp_path / "digital_book",
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["standard_outline"]["schema_version"] == "materials2textbook.standard_outline.v1"
    assert payload["standard_outline"]["projects"][0]["display_number"] == "项目一"
    assert payload["outline_validation"]["release_allowed"] is True


def test_outline_overlay_does_not_mutate_canonical_book() -> None:
    book = _book()
    before = json.loads(json.dumps(book, ensure_ascii=False))
    generate_standard_outline(book)
    validate_outline(generate_standard_outline(book), book)
    assert book == before
