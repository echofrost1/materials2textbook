from __future__ import annotations

from collections import defaultdict

from materials2textbook.schemas import ChapterPlan, EvidenceChunk, KnowledgePoint


class KnowledgeOrganizerAgent:
    """Group evidence chunks into a simple chapter plan."""

    def run(self, chunks: list[EvidenceChunk]) -> list[ChapterPlan]:
        by_chapter: dict[str, list[EvidenceChunk]] = defaultdict(list)
        for chunk in chunks:
            by_chapter[chunk.recommended_chapter or "待规划章节"].append(chunk)

        plans: list[ChapterPlan] = []
        for index, (chapter_title, chapter_chunks) in enumerate(by_chapter.items(), start=1):
            by_point: dict[str, list[EvidenceChunk]] = defaultdict(list)
            for chunk in chapter_chunks:
                by_point[chunk.title].append(chunk)

            points = [
                KnowledgePoint(
                    knowledge_point_id=f"kp_{index:02d}_{point_index:02d}",
                    title=point_title,
                    chunk_ids=[chunk.chunk_id for chunk in point_chunks],
                    summary=point_chunks[0].summary,
                )
                for point_index, (point_title, point_chunks) in enumerate(by_point.items(), start=1)
            ]

            plans.append(
                ChapterPlan(
                    chapter_id=f"chapter_{index:02d}",
                    title=chapter_title,
                    learning_goals=[
                        f"理解{chapter_title}的核心概念和适用场景",
                        f"能够根据素材证据复述{chapter_title}的关键操作或原理",
                        "能够识别需要人工复核的资料片段",
                    ],
                    knowledge_points=points,
                    evidence_chunk_ids=[chunk.chunk_id for chunk in chapter_chunks],
                    activities=[
                        "结合视频片段观察关键动作或现象",
                        "根据证据片段回答思考题",
                    ],
                )
            )
        return plans
