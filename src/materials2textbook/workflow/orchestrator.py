from __future__ import annotations

from pathlib import Path

from materials2textbook.agents.knowledge_organizer import KnowledgeOrganizerAgent
from materials2textbook.agents.resource_analyst import ResourceAnalystAgent
from materials2textbook.agents.reviewers import EvidenceReviewerAgent, PedagogyReviewerAgent, ReviewComposer
from materials2textbook.agents.revision import RevisionAgent
from materials2textbook.agents.textbook_writer import TextbookWriterAgent
from materials2textbook.io_utils import read_jsonl, write_json, write_jsonl, write_text
from materials2textbook.schemas import WorkflowOutputs


class TextbookWorkflow:
    """Run the first multi-agent orchestration loop over processed material segments."""

    def __init__(self) -> None:
        self.resource_analyst = ResourceAnalystAgent()
        self.organizer = KnowledgeOrganizerAgent()
        self.writer = TextbookWriterAgent()
        self.evidence_reviewer = EvidenceReviewerAgent()
        self.pedagogy_reviewer = PedagogyReviewerAgent()
        self.review_composer = ReviewComposer()
        self.revision = RevisionAgent()

    def run(self, video_segments_path: Path, output_dir: Path, title: str) -> WorkflowOutputs:
        records = read_jsonl(video_segments_path)

        chunks = self.resource_analyst.run(records)
        plans = self.organizer.run(chunks)
        draft = self.writer.run(plans, chunks, title=title)

        fact_issues = self.evidence_reviewer.run(plans, chunks)
        pedagogy_issues = self.pedagogy_reviewer.run(plans)
        reports = self.review_composer.run(plans, fact_issues, pedagogy_issues)
        final = self.revision.run(draft, reports)

        evidence_chunks_path = output_dir / "evidence_chunks.jsonl"
        chapter_plan_path = output_dir / "chapter_plan.json"
        draft_path = output_dir / "textbook_draft.md"
        review_report_path = output_dir / "review_report.json"
        final_path = output_dir / "textbook_final.md"

        write_jsonl(evidence_chunks_path, chunks)
        write_json(chapter_plan_path, plans)
        write_text(draft_path, draft)
        write_json(review_report_path, reports)
        write_text(final_path, final)

        return WorkflowOutputs(
            evidence_chunks_path=str(evidence_chunks_path),
            chapter_plan_path=str(chapter_plan_path),
            draft_path=str(draft_path),
            review_report_path=str(review_report_path),
            final_path=str(final_path),
        )
