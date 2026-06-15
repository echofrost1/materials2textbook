from __future__ import annotations

from materials2textbook.adapters.video_segments import video_segment_to_evidence_chunk
from materials2textbook.schemas import EvidenceChunk


class ResourceAnalystAgent:
    """Convert upstream segment records into normalized evidence chunks."""

    def run(self, video_segments: list[dict]) -> list[EvidenceChunk]:
        chunks = [video_segment_to_evidence_chunk(record) for record in video_segments]
        return [chunk for chunk in chunks if chunk.chunk_id and chunk.asset_id]
