"""Unit tests for validation business rules and decision engine.

Covers Task 19 (regression suite).
"""

import pytest

from api.models.db import DocumentType
from workers.validation.tasks import validate_business_rules, validate_schema


# ---------------------------------------------------------------------------
# validate_business_rules
# ---------------------------------------------------------------------------

class TestValidateBusinessRules:
    def test_valid_invoice_no_violations(self, invoice_extracted_data):
        violations = validate_business_rules(invoice_extracted_data, DocumentType.INVOICE)
        # All required fields present, dates valid, amounts positive
        assert violations == []

    def test_missing_invoice_number(self, invoice_extracted_data):
        data = dict(invoice_extracted_data)
        del data["invoice_number"]
        violations = validate_business_rules(data, DocumentType.INVOICE)
        rules = [v["rule"] for v in violations]
        assert "required_field" in rules

    def test_missing_invoice_date(self, invoice_extracted_data):
        data = dict(invoice_extracted_data)
        del data["invoice_date"]
        violations = validate_business_rules(data, DocumentType.INVOICE)
        rules = [v["rule"] for v in violations]
        assert "required_field" in rules

    def test_missing_totals(self, invoice_extracted_data):
        data = dict(invoice_extracted_data)
        del data["totals"]
        violations = validate_business_rules(data, DocumentType.INVOICE)
        rules = [v["rule"] for v in violations]
        assert "required_field" in rules

    def test_negative_grand_total(self, invoice_extracted_data):
        import copy
        data = copy.deepcopy(invoice_extracted_data)
        data["totals"]["grand_total"] = -100.0
        violations = validate_business_rules(data, DocumentType.INVOICE)
        rules = [v["rule"] for v in violations]
        assert "negative_amount" in rules

    def test_invalid_date_format(self, invoice_extracted_data):
        import copy
        data = copy.deepcopy(invoice_extracted_data)
        data["invoice_date"] = "not-a-date"
        violations = validate_business_rules(data, DocumentType.INVOICE)
        rules = [v["rule"] for v in violations]
        assert "date_format" in rules

    def test_valid_iso_date_accepted(self, invoice_extracted_data):
        import copy
        data = copy.deepcopy(invoice_extracted_data)
        data["invoice_date"] = "2024-03-15"
        violations = validate_business_rules(data, DocumentType.INVOICE)
        date_violations = [v for v in violations if v["rule"] == "date_format"]
        assert date_violations == []

    def test_valid_serbian_date_accepted(self, invoice_extracted_data):
        import copy
        data = copy.deepcopy(invoice_extracted_data)
        data["invoice_date"] = "15.03.2024"
        violations = validate_business_rules(data, DocumentType.INVOICE)
        date_violations = [v for v in violations if v["rule"] == "date_format"]
        assert date_violations == []

    def test_invalid_pib_format(self, invoice_extracted_data):
        import copy
        data = copy.deepcopy(invoice_extracted_data)
        data["pib"] = "123"  # Too short
        violations = validate_business_rules(data, DocumentType.INVOICE)
        rules = [v["rule"] for v in violations]
        assert "pib_format" in rules

    def test_valid_pib_nine_digits(self, invoice_extracted_data):
        import copy
        data = copy.deepcopy(invoice_extracted_data)
        data["pib"] = "123456789"
        violations = validate_business_rules(data, DocumentType.INVOICE)
        pib_violations = [v for v in violations if v["rule"] == "pib_format"]
        assert pib_violations == []

    def test_proforma_required_fields(self):
        data = {"issue_date": "2024-03-10"}  # Missing proforma_number
        violations = validate_business_rules(data, DocumentType.PROFORMA)
        rules = [v["rule"] for v in violations]
        assert "required_field" in rules

    def test_delivery_note_required_fields(self):
        data = {"issue_date": "2024-03-20"}  # Missing delivery_note_number
        violations = validate_business_rules(data, DocumentType.DELIVERY_NOTE)
        rules = [v["rule"] for v in violations]
        assert "required_field" in rules

    def test_contract_required_fields(self):
        data = {"issue_date": "2024-01-01"}  # Missing contract_number
        violations = validate_business_rules(data, DocumentType.CONTRACT)
        rules = [v["rule"] for v in violations]
        assert "required_field" in rules

    def test_bank_statement_no_required_fields(self):
        data = {"balance": 1000.0}
        violations = validate_business_rules(data, DocumentType.BANK_STATEMENT)
        required_violations = [v for v in violations if v["rule"] == "required_field"]
        assert required_violations == []

    def test_nested_grand_total_checked(self, invoice_extracted_data):
        import copy
        data = copy.deepcopy(invoice_extracted_data)
        data["totals"]["grand_total"] = -50.0
        violations = validate_business_rules(data, DocumentType.INVOICE)
        negative_violations = [v for v in violations if v["rule"] == "negative_amount"]
        assert len(negative_violations) >= 1

    def test_amount_string_with_comma_handled(self, invoice_extracted_data):
        import copy
        data = copy.deepcopy(invoice_extracted_data)
        data["totals"]["grand_total"] = "96,000.00"
        violations = validate_business_rules(data, DocumentType.INVOICE)
        amount_errors = [v for v in violations if v["rule"] in ("invalid_amount", "negative_amount")]
        assert amount_errors == []


# ---------------------------------------------------------------------------
# validate_schema (JSON schema validation)
# ---------------------------------------------------------------------------

class TestValidateSchema:
    def test_valid_data_passes_empty_schema(self, invoice_extracted_data):
        schema = {"$schema": "...", "type": "object", "properties": {}}
        valid, errors = validate_schema(invoice_extracted_data, schema)
        assert valid is True
        assert errors == []

    def test_wrong_type_fails(self):
        schema = {
            "type": "object",
            "properties": {"invoice_number": {"type": "string"}},
        }
        data = {"invoice_number": 12345}  # Should be string
        valid, errors = validate_schema(data, schema)
        assert valid is False
        assert len(errors) > 0

    def test_correct_type_passes(self):
        schema = {
            "type": "object",
            "properties": {"invoice_number": {"type": "string"}},
        }
        data = {"invoice_number": "INV-001"}
        valid, errors = validate_schema(data, schema)
        assert valid is True

    def test_errors_contain_field_name(self):
        schema = {
            "type": "object",
            "properties": {"amount": {"type": "number", "minimum": 0}},
        }
        data = {"amount": -5}
        valid, errors = validate_schema(data, schema)
        assert not valid
        assert any("amount" in e["field"] or e["field"] == "amount" or e["field"] == "root"
                   for e in errors)
