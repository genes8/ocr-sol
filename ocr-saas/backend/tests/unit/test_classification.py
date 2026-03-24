"""Unit tests for document classification logic.

Covers Task 19 (regression suite) — classification for all 6 doc types,
plus Task 17 (explicit unknown routing).
"""

import pytest

from workers.classification.tasks import (
    ClassificationResult,
    classify_document,
)
from api.models.db import DocumentType


class TestClassifyDocument:
    def test_invoice_detected(self, invoice_text):
        result = classify_document(invoice_text)
        assert result.document_type == DocumentType.INVOICE
        assert result.confidence >= 0.35
        assert len(result.matched_patterns) > 0

    def test_proforma_detected(self, proforma_text):
        result = classify_document(proforma_text)
        assert result.document_type == DocumentType.PROFORMA
        assert result.confidence >= 0.35

    def test_delivery_note_detected(self, delivery_note_text):
        result = classify_document(delivery_note_text)
        assert result.document_type == DocumentType.DELIVERY_NOTE
        assert result.confidence >= 0.35

    def test_contract_detected(self, contract_text):
        result = classify_document(contract_text)
        assert result.document_type == DocumentType.CONTRACT
        assert result.confidence >= 0.35

    def test_bank_statement_detected(self):
        text = """
        IZVOD IZ RAČUNA
        Banka Srbije
        Promet: 150.000 RSD
        Stanje: 250.000 RSD
        Uplatnica broj: 12345
        """
        result = classify_document(text)
        assert result.document_type == DocumentType.BANK_STATEMENT
        assert result.confidence >= 0.35

    def test_official_document_detected(self):
        text = """
        Решење broj: RS-2024-001
        Potvrda o registraciji
        Obrazac APR-01
        """
        result = classify_document(text)
        assert result.document_type == DocumentType.OFFICIAL_DOCUMENT
        assert result.confidence >= 0.35

    # --- Task 17: Unknown routing ---

    def test_unknown_text_returns_zero_confidence(self, unknown_text):
        """No keyword matches → confidence 0.0 → explicit unknown routing."""
        result = classify_document(unknown_text)
        assert result.confidence == 0.0
        assert result.matched_patterns == []

    def test_unknown_reasoning_message(self, unknown_text):
        result = classify_document(unknown_text)
        assert "unknown" in result.reasoning.lower() or "no" in result.reasoning.lower()

    def test_confidence_in_range(self, invoice_text):
        result = classify_document(invoice_text)
        assert 0.0 <= result.confidence <= 1.0

    def test_invoice_beats_proforma_on_invoice_text(self, invoice_text):
        result = classify_document(invoice_text)
        assert result.document_type == DocumentType.INVOICE

    def test_matched_patterns_are_strings(self, invoice_text):
        result = classify_document(invoice_text)
        assert all(isinstance(p, str) for p in result.matched_patterns)

    def test_reasoning_is_string(self, invoice_text):
        result = classify_document(invoice_text)
        assert isinstance(result.reasoning, str)
