"""Read-only whole-book instructional availability analysis."""

from materials2textbook.knowledge_map.pipeline import (
    analyze_book_knowledge,
    write_knowledge_map_artifacts,
)

__all__ = ["analyze_book_knowledge", "write_knowledge_map_artifacts"]
