"""Regression / golden-output tests for structuring normalization (Task 19).

Tests that normalize_extracted_data() produces the expected canonical output
for LLM responses for each document type. These act as golden tests: if the
normalization logic changes in a breaking way, these fail.
"""

import copy

import pytest

from api.models.db import DocumentType
from workers.structuring.tasks import normalize_extracted_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize(data: dict, doc_type: DocumentType) -> dict:
    return normalize_extracted_data(copy.deepcopy(data), doc_type)


# ---------------------------------------------------------------------------
# INVOICE — line items canonical keys
# ---------------------------------------------------------------------------

class TestInvoiceNormalization:
    def test_line_items_canonical_keys_preserved(self):
        raw = {
            "invoice_number": "2024-001",
            "invoice_date": "2024-03-15",
            "totals": {"grand_total": 96000.0, "vat_amount": 16000.0, "subtotal": 80000.0},
            "line_items": [
                {"description": "Usluga", "quantity": 10, "unit_price": 5000, "line_total": 50000},
            ],
        }
        out = normalize(raw, DocumentType.INVOICE)
        item = out["line_items"][0]
        assert item["description"] == "Usluga"
        assert item["quantity"] == 10
        assert item["unit_price"] == 5000
        assert item["line_total"] == 50000

    def test_line_item_alias_name_mapped(self):
        raw = {
            "invoice_number": "001",
            "line_items": [
                {"name": "Roba A", "qty": 5, "price": 1000, "total": 5000},
            ],
        }
        out = normalize(raw, DocumentType.INVOICE)
        item = out["line_items"][0]
        assert item["description"] == "Roba A"
        assert item["quantity"] == 5
        assert item["unit_price"] == 1000
        assert item["line_total"] == 5000

    def test_garbage_raw_text_items_filtered(self):
        raw = {
            "invoice_number": "001",
            "line_items": [
                {"raw_text": "some text", "values": ["10", "50"]},
                {"description": "Real item", "quantity": 1, "unit_price": 100, "line_total": 100},
            ],
        }
        out = normalize(raw, DocumentType.INVOICE)
        assert len(out["line_items"]) == 1
        assert out["line_items"][0]["description"] == "Real item"

    def test_extra_item_fields_preserved(self):
        raw = {
            "line_items": [
                {"description": "X", "quantity": 1, "unit_price": 10,
                 "line_total": 10, "vat_rate": 0.20, "sku": "ABC-01"},
            ],
        }
        out = normalize(raw, DocumentType.INVOICE)
        assert out["line_items"][0]["vat_rate"] == 0.20
        assert out["line_items"][0]["sku"] == "ABC-01"

    def test_top_level_fields_unchanged(self):
        raw = {
            "invoice_number": "INV-2024-042",
            "invoice_date": "2024-03-15",
            "supplier": {"name": "ACME", "pib": "101234567"},
            "totals": {"grand_total": 5000.0},
        }
        out = normalize(raw, DocumentType.INVOICE)
        assert out["invoice_number"] == "INV-2024-042"
        assert out["supplier"]["pib"] == "101234567"

    def test_empty_line_items_list_preserved(self):
        raw = {"invoice_number": "001", "line_items": []}
        out = normalize(raw, DocumentType.INVOICE)
        assert out["line_items"] == []


# ---------------------------------------------------------------------------
# PROFORMA — no line-item transformation required
# ---------------------------------------------------------------------------

class TestProformaNormalization:
    def test_proforma_fields_pass_through(self):
        raw = {
            "proforma_number": "PF-2024-007",
            "issue_date": "2024-03-10",
            "valid_until": "2024-04-10",
            "totals": {"grand_total": 120000.0},
        }
        out = normalize(raw, DocumentType.PROFORMA)
        assert out["proforma_number"] == "PF-2024-007"
        assert out["issue_date"] == "2024-03-10"
        assert out["totals"]["grand_total"] == 120000.0

    def test_proforma_line_items_normalized_if_present(self):
        raw = {
            "proforma_number": "PF-001",
            "line_items": [
                {"name": "Service A", "qty": 2, "price": 500, "amount": 1000},
            ],
        }
        out = normalize(raw, DocumentType.PROFORMA)
        item = out["line_items"][0]
        assert item["description"] == "Service A"
        assert item["line_total"] == 1000


# ---------------------------------------------------------------------------
# DELIVERY NOTE — `items` → `line_items` mapping
# ---------------------------------------------------------------------------

class TestDeliveryNoteNormalization:
    def test_items_renamed_to_line_items(self):
        raw = {
            "delivery_note_number": "OT-2024-101",
            "issue_date": "2024-03-20",
            "items": [
                {"description": "Roba A", "quantity": 100, "unit": "kom"},
                {"description": "Roba B", "quantity": 50, "unit": "kom"},
            ],
        }
        out = normalize(raw, DocumentType.DELIVERY_NOTE)
        assert "line_items" in out
        assert "items" not in out
        assert len(out["line_items"]) == 2

    def test_delivery_item_canonical_keys(self):
        raw = {
            "items": [
                {"description": "Widget", "delivered_quantity": 10, "unit": "pcs"},
            ],
        }
        out = normalize(raw, DocumentType.DELIVERY_NOTE)
        item = out["line_items"][0]
        assert item["description"] == "Widget"
        assert item["quantity"] == 10  # from delivered_quantity alias
        assert item["unit"] == "pcs"

    def test_existing_line_items_not_duplicated(self):
        """If LLM already returned line_items, don't double-map."""
        raw = {
            "line_items": [
                {"description": "Direct item", "quantity": 5},
            ],
        }
        out = normalize(raw, DocumentType.DELIVERY_NOTE)
        assert len(out["line_items"]) == 1
        assert out["line_items"][0]["description"] == "Direct item"

    def test_delivery_item_batch_number_preserved(self):
        raw = {
            "items": [
                {"description": "Drug X", "quantity": 200, "batch_number": "B2024-01"},
            ],
        }
        out = normalize(raw, DocumentType.DELIVERY_NOTE)
        assert out["line_items"][0]["batch_number"] == "B2024-01"


# ---------------------------------------------------------------------------
# CONTRACT — no line items, fields pass through
# ---------------------------------------------------------------------------

class TestContractNormalization:
    def test_contract_fields_pass_through(self):
        raw = {
            "contract_number": "UG-2024-001",
            "issue_date": "2024-01-01",
            "parties": ["ACME d.o.o.", "Beta firma d.o.o."],
            "subject": "Softverske usluge",
        }
        out = normalize(raw, DocumentType.CONTRACT)
        assert out["contract_number"] == "UG-2024-001"
        assert out["parties"] == ["ACME d.o.o.", "Beta firma d.o.o."]


# ---------------------------------------------------------------------------
# BANK STATEMENT — no line items
# ---------------------------------------------------------------------------

class TestBankStatementNormalization:
    def test_bank_statement_pass_through(self):
        raw = {
            "account_number": "160-123456-77",
            "balance": 250000.0,
            "transactions": [
                {"date": "2024-03-01", "amount": 50000.0, "type": "credit"},
            ],
        }
        out = normalize(raw, DocumentType.BANK_STATEMENT)
        assert out["balance"] == 250000.0
        assert len(out["transactions"]) == 1


# ---------------------------------------------------------------------------
# OFFICIAL DOCUMENT — no line items
# ---------------------------------------------------------------------------

class TestOfficialDocumentNormalization:
    def test_official_document_pass_through(self):
        raw = {
            "document_number": "RS-2024-001",
            "issue_date": "2024-01-15",
            "issuing_authority": "Ministarstvo finansija",
            "document_type_label": "Rešenje",
        }
        out = normalize(raw, DocumentType.OFFICIAL_DOCUMENT)
        assert out["document_number"] == "RS-2024-001"
        assert out["issuing_authority"] == "Ministarstvo finansija"


# ---------------------------------------------------------------------------
# Edge cases — shared
# ---------------------------------------------------------------------------

class TestNormalizationEdgeCases:
    def test_none_quantity_preserved(self):
        raw = {
            "line_items": [{"description": "X", "unit_price": 100, "line_total": 100}],
        }
        out = normalize(raw, DocumentType.INVOICE)
        assert out["line_items"][0]["quantity"] is None

    def test_original_data_not_mutated(self):
        original = {
            "items": [{"description": "A", "quantity": 1}],
        }
        before = copy.deepcopy(original)
        normalize(original, DocumentType.DELIVERY_NOTE)
        assert original == before  # deep copy, original unchanged

    def test_empty_data_handled(self):
        out = normalize({}, DocumentType.INVOICE)
        assert isinstance(out, dict)

    def test_no_line_items_key_added_for_non_item_types(self):
        """CONTRACT and BANK_STATEMENT should not get a line_items key."""
        out = normalize({"contract_number": "C-001"}, DocumentType.CONTRACT)
        assert "line_items" not in out
