"""Shared pytest fixtures."""

import uuid
import pytest


@pytest.fixture
def sample_tenant_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def sample_document_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Sample OCR full-text fixtures for each document type
# ---------------------------------------------------------------------------

@pytest.fixture
def invoice_text() -> str:
    return """
    FAKTURA broj: 2024-0042
    Dobavljač: ACME d.o.o. Beograd
    PIB: 101234567
    Kupac: Beta firma d.o.o.
    Datum fakture (invoice_date): 15.03.2024.
    Rok plaćanja (due_date): 15.04.2024.

    Stavke:
    1. Usluga consultinga         10 h × 5.000 RSD = 50.000 RSD
    2. Licenca softvera           1 kom × 30.000 RSD = 30.000 RSD

    Subtotal (osnova): 80.000 RSD
    PDV 20%:          16.000 RSD
    Ukupno za plaćanje: 96.000 RSD
    """


@pytest.fixture
def proforma_text() -> str:
    return """
    PROFORMA FAKTURA / PONUDA
    Broj: PF-2024-007
    Datum: 10.03.2024
    issue_date: 10.03.2024
    valid_until: 10.04.2024

    Ponuđač: Firma X d.o.o.
    PIB ponuđača: 987654321

    Ukupna vrednost ponude: 120.000 RSD
    PDV (20%): 24.000 RSD
    """


@pytest.fixture
def delivery_note_text() -> str:
    return """
    OTPREMNICA br. OT-2024-101
    Datum: 20.03.2024
    issue_date: 20.03.2024

    Pošiljalac: Distribucija d.o.o.
    Primalac: Kupac firma d.o.o.

    Stavke:
    - Roba A   qty: 100 kom
    - Roba B   qty: 50 kom
    """


@pytest.fixture
def contract_text() -> str:
    return """
    UGOVOR O PRUŽANJU USLUGA
    Broj ugovora: UG-2024-001
    Datum zaključenja: 01.01.2024
    issue_date: 01.01.2024

    Ugovorne strane:
    1. ACME d.o.o. (Pružalac usluga)
    2. Beta firma d.o.o. (Korisnik)

    Predmet ugovora: Softverske usluge
    Trajanje: 12 meseci
    """


@pytest.fixture
def unknown_text() -> str:
    return "Lorem ipsum dolor sit amet. Nema prepoznatljivih ključnih reči."


@pytest.fixture
def invoice_extracted_data() -> dict:
    return {
        "invoice_number": "2024-0042",
        "invoice_date": "2024-03-15",
        "due_date": "2024-04-15",
        "supplier": {
            "name": "ACME d.o.o.",
            "pib": "101234567",
        },
        "totals": {
            "subtotal": 80000.0,
            "vat_amount": 16000.0,
            "grand_total": 96000.0,
        },
        "line_items": [
            {"description": "Usluga consultinga", "quantity": 10, "unit_price": 5000, "line_total": 50000},
            {"description": "Licenca softvera", "quantity": 1, "unit_price": 30000, "line_total": 30000},
        ],
    }


@pytest.fixture
def invoice_confidences() -> dict:
    return {
        "invoice_number": 0.95,
        "invoice_date": 0.93,
        "due_date": 0.88,
        "supplier_name": 0.91,
        "supplier_pib": 0.97,
        "totals.grand_total": 0.94,
        "totals.vat_amount": 0.90,
        "totals.subtotal": 0.89,
    }
