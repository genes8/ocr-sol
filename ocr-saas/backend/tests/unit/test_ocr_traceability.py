"""Unit tests for OCR traceability — stable block IDs (Task 18)."""

import pytest

from workers.ocr.tasks import _stable_block_id


class TestStableBlockId:
    def test_same_input_same_id(self):
        bbox = {"x1": 10, "y1": 20, "x2": 200, "y2": 40}
        id1 = _stable_block_id(1, "Invoice Number", bbox)
        id2 = _stable_block_id(1, "Invoice Number", bbox)
        assert id1 == id2

    def test_different_page_different_id(self):
        bbox = {"x1": 10, "y1": 20, "x2": 200, "y2": 40}
        id1 = _stable_block_id(1, "Total", bbox)
        id2 = _stable_block_id(2, "Total", bbox)
        assert id1 != id2

    def test_different_text_different_id(self):
        bbox = {"x1": 10, "y1": 20, "x2": 200, "y2": 40}
        id1 = _stable_block_id(1, "Invoice Number", bbox)
        id2 = _stable_block_id(1, "Total Amount", bbox)
        assert id1 != id2

    def test_different_bbox_different_id(self):
        id1 = _stable_block_id(1, "Text", {"x1": 10, "y1": 20, "x2": 200, "y2": 40})
        id2 = _stable_block_id(1, "Text", {"x1": 50, "y1": 20, "x2": 200, "y2": 40})
        assert id1 != id2

    def test_id_starts_with_prefix(self):
        bbox = {"x1": 0, "y1": 0, "x2": 100, "y2": 20}
        block_id = _stable_block_id(1, "Test", bbox)
        assert block_id.startswith("blk-")

    def test_id_is_fixed_length(self):
        bbox = {"x1": 0, "y1": 0, "x2": 100, "y2": 20}
        id1 = _stable_block_id(1, "Short text", bbox)
        id2 = _stable_block_id(1, "A" * 200, bbox)
        assert len(id1) == len(id2)

    def test_empty_bbox_values_handled(self):
        # Should not raise
        block_id = _stable_block_id(1, "Text", {})
        assert block_id.startswith("blk-")

    def test_long_text_truncated_consistently(self):
        """Two texts that differ only after char 80 produce same ID."""
        bbox = {"x1": 0, "y1": 0, "x2": 100, "y2": 20}
        long_text_a = "A" * 80 + "X"
        long_text_b = "A" * 80 + "Y"
        id1 = _stable_block_id(1, long_text_a, bbox)
        id2 = _stable_block_id(1, long_text_b, bbox)
        assert id1 == id2  # Truncated at 80 chars

    def test_retry_produces_same_id(self):
        """Simulates retry: same block data → same ID, no duplication."""
        bbox = {"x1": 100, "y1": 50, "x2": 400, "y2": 70}
        text = "2024-0042"
        id_first_run = _stable_block_id(1, text, bbox)
        id_retry_run = _stable_block_id(1, text, bbox)
        assert id_first_run == id_retry_run
