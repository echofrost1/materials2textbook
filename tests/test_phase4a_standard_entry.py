from __future__ import annotations

from scripts.run_full_digital_textbook import DEFAULT_SEMANTIC_BOOK_MODE as FULL_DEFAULT
from scripts.run_topic_textbook import DEFAULT_SEMANTIC_BOOK_MODE as TOPIC_DEFAULT


def test_whole_book_production_scripts_default_to_semantic_closed_loop() -> None:
    assert FULL_DEFAULT is True
    assert TOPIC_DEFAULT is True
