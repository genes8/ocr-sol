"""Reconciliation worker tasks - Line items math validation."""

import logging
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.core.database import get_db_session
from api.models.db import (
    Document,
    DocumentStatus,
    DocumentType,
    ReconciliationLog,
    StructuredResult,
)
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# get_db_session imported from api.core.database


def update_document_status(
    document_id: str,
    status: DocumentStatus,
    error_message: str | None = None,
) -> None:
    """Update document status in database."""
    import asyncio

    async def _update():
        session = await get_db_session()
        try:
            result = await session.execute(
                select(Document).where(Document.id == uuid.UUID(document_id))
            )
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = status
                doc.error_message = error_message
                await session.commit()
        finally:
            await session.close()

    asyncio.run(_update())


def parse_amount(value: Any) -> Decimal | None:
    """Parse amount from various formats."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return Decimal(str(value))

    if isinstance(value, str):
        cleaned = value.replace("€", "").replace("$", "").replace("RSD", "")
        cleaned = cleaned.replace(" ", "").replace("\xa0", "")
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            parts = cleaned.split(",")
            if len(parts[-1]) == 2:
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")

        try:
            return Decimal(cleaned)
        except Exception:
            return None

    return None


def _get_extracted_totals(extracted_data: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Read totals from extracted_data, supporting both flat and nested schema paths."""
    totals = extracted_data.get("totals", {}) or {}

    extracted_subtotal = parse_amount(
        totals.get("subtotal") or extracted_data.get("subtotal")
    )
    extracted_vat = parse_amount(
        totals.get("vat_total") or extracted_data.get("vat_total") or extracted_data.get("vat_amount")
    )
    extracted_total = parse_amount(
        totals.get("grand_total") or extracted_data.get("grand_total") or extracted_data.get("total_amount")
    )
    return extracted_subtotal, extracted_vat, extracted_total


def reconcile_line_items(
    extracted_data: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile line items and calculate totals with per-VAT-rate grouping.

    Groups line items by their individual vat_rate (0%, 10%, 20%) and
    calculates per-group taxable/VAT amounts. Compares per-group totals
    against extracted vat_breakdown array if present.
    """
    line_items = extracted_data.get("line_items", [])
    extracted_subtotal, extracted_vat, extracted_total = _get_extracted_totals(extracted_data)
    extracted_vat_breakdown = extracted_data.get("vat_breakdown", []) or []

    # Groups keyed by VAT rate (int %): {rate: {taxable_amount, vat_amount}}
    vat_groups: dict[int, dict[str, Decimal]] = {}

    reconciled_items = []

    for item in line_items:
        qty = parse_amount(item.get("quantity", 1))
        unit_price = parse_amount(item.get("unit_price"))
        line_total = parse_amount(item.get("line_total"))
        discount_amount = parse_amount(item.get("discount_amount", 0))
        discount_pct = parse_amount(item.get("discount_pct", 0))
        item_vat_rate = parse_amount(item.get("vat_rate", 20))
        vat_rate_int = int(item_vat_rate) if item_vat_rate is not None else 20

        if unit_price is not None:
            if qty is None:
                qty = Decimal("1")

            net_amount = qty * unit_price

            # Resolve discount
            if discount_amount and discount_amount > 0:
                discount = discount_amount
            elif discount_pct and discount_pct > 0:
                discount = (net_amount * discount_pct / 100).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                discount = Decimal("0")

            net_after_discount = net_amount - discount
            vat_for_line = (net_after_discount * Decimal(str(vat_rate_int)) / 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            calc_total = net_after_discount

            match = True
            if line_total is not None:
                match = abs(calc_total - line_total) < Decimal("0.01")

            reconciled_items.append({
                "description": item.get("description", item.get("raw_text", "")),
                "quantity": float(qty),
                "unit_price": float(unit_price),
                "vat_rate": vat_rate_int,
                "line_total": float(line_total) if line_total else None,
                "calculated_total": float(calc_total),
                "match": match,
            })

            # Accumulate into VAT group
            if vat_rate_int not in vat_groups:
                vat_groups[vat_rate_int] = {"taxable_amount": Decimal("0"), "vat_amount": Decimal("0")}
            vat_groups[vat_rate_int]["taxable_amount"] += net_after_discount
            vat_groups[vat_rate_int]["vat_amount"] += vat_for_line

        elif line_total is not None:
            calc_total = line_total
            reconciled_items.append({
                "description": item.get("description", item.get("raw_text", "")),
                "quantity": None,
                "unit_price": None,
                "vat_rate": vat_rate_int,
                "line_total": float(line_total),
                "calculated_total": float(line_total),
                "match": True,
            })
            if vat_rate_int not in vat_groups:
                vat_groups[vat_rate_int] = {"taxable_amount": Decimal("0"), "vat_amount": Decimal("0")}
            vat_rate_decimal = Decimal(str(vat_rate_int))
            vat_for_line = (line_total * vat_rate_decimal / 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            vat_groups[vat_rate_int]["taxable_amount"] += line_total
            vat_groups[vat_rate_int]["vat_amount"] += vat_for_line

    # Aggregate across all groups
    calculated_subtotal = sum(
        (g["taxable_amount"] for g in vat_groups.values()), start=Decimal("0")
    )
    calculated_vat = sum(
        (g["vat_amount"] for g in vat_groups.values()), start=Decimal("0")
    )
    calculated_total = calculated_subtotal + calculated_vat

    # Build per-group comparison against extracted vat_breakdown
    # vat_breakdown expected format: [{"vat_rate": 20, "taxable_amount": ..., "vat_amount": ...}, ...]
    breakdown_by_rate: dict[int, dict[str, Any]] = {}
    for entry in extracted_vat_breakdown:
        rate = int(parse_amount(entry.get("vat_rate", 20)) or 20)
        breakdown_by_rate[rate] = entry

    vat_groups_result: dict[str, Any] = {}
    for rate, group in vat_groups.items():
        extracted_entry = breakdown_by_rate.get(rate, {})
        extracted_taxable = parse_amount(extracted_entry.get("taxable_amount"))
        extracted_vat_g = parse_amount(extracted_entry.get("vat_amount"))

        taxable_match = (
            extracted_taxable is not None
            and abs(group["taxable_amount"] - extracted_taxable) < Decimal("0.01")
        )
        vat_match_g = (
            extracted_vat_g is not None
            and abs(group["vat_amount"] - extracted_vat_g) < Decimal("0.01")
        )

        vat_groups_result[str(rate)] = {
            "taxable_amount": float(group["taxable_amount"]),
            "calc_vat": float(group["vat_amount"]),
            "extracted_taxable": float(extracted_taxable) if extracted_taxable else None,
            "extracted_vat": float(extracted_vat_g) if extracted_vat_g else None,
            "taxable_match": taxable_match,
            "vat_match": vat_match_g,
        }

    # Overall match checks
    subtotal_match = (
        extracted_subtotal is not None
        and abs(calculated_subtotal - extracted_subtotal) < Decimal("0.01")
    )
    vat_match = (
        extracted_vat is not None
        and abs(calculated_vat - extracted_vat) < Decimal("0.01")
    )
    total_match = (
        extracted_total is not None
        and abs(calculated_total - extracted_total) < Decimal("0.01")
    )

    # Determine reconciliation status
    if subtotal_match and total_match:
        if vat_match:
            status = "pass"
        else:
            status = "warn"
    else:
        status = "fail"

    discrepancy_details: dict[str, Any] = {}

    if not subtotal_match:
        discrepancy_details["subtotal"] = {
            "extracted": float(extracted_subtotal) if extracted_subtotal else None,
            "calculated": float(calculated_subtotal),
            "difference": float(extracted_subtotal - calculated_subtotal) if extracted_subtotal else None,
        }

    if not vat_match:
        discrepancy_details["vat"] = {
            "extracted": float(extracted_vat) if extracted_vat else None,
            "calculated": float(calculated_vat),
            "difference": float(extracted_vat - calculated_vat) if extracted_vat else None,
        }

    if not total_match:
        discrepancy_details["total"] = {
            "extracted": float(extracted_total) if extracted_total else None,
            "calculated": float(calculated_total),
            "difference": float(extracted_total - calculated_total) if extracted_total else None,
        }

    # Feature 3: Include per-VAT-rate groups in discrepancy_details
    discrepancy_details["vat_groups"] = vat_groups_result

    return {
        "status": status,
        "line_items_count": len(line_items),
        "reconciled_items": reconciled_items,
        "extracted_subtotal": float(extracted_subtotal) if extracted_subtotal else None,
        "calculated_subtotal": float(calculated_subtotal),
        "subtotal_match": subtotal_match,
        "extracted_vat": float(extracted_vat) if extracted_vat else None,
        "calculated_vat": float(calculated_vat),
        "vat_match": vat_match,
        "extracted_total": float(extracted_total) if extracted_total else None,
        "calculated_total": float(calculated_total),
        "total_match": total_match,
        "discrepancy_details": discrepancy_details,
    }


async def save_reconciliation_log(
    document_id: str,
    reconciliation: dict[str, Any],
    processing_time_ms: int,
) -> str:
    """Save reconciliation log to database."""
    session = await get_db_session()
    try:
        log = ReconciliationLog(
            id=uuid.uuid4(),
            document_id=uuid.UUID(document_id),
            line_items_count=reconciliation["line_items_count"],
            extracted_subtotal=reconciliation.get("extracted_subtotal"),
            calculated_subtotal=reconciliation.get("calculated_subtotal"),
            extracted_vat=reconciliation.get("extracted_vat"),
            calculated_vat=reconciliation.get("calculated_vat"),
            extracted_total=reconciliation.get("extracted_total"),
            calculated_total=reconciliation.get("calculated_total"),
            subtotal_match=reconciliation.get("subtotal_match"),
            vat_match=reconciliation.get("vat_match"),
            total_match=reconciliation.get("total_match"),
            reconciliation_status=reconciliation["status"],
            discrepancy_details=reconciliation.get("discrepancy_details"),
            processing_time_ms=processing_time_ms,
        )
        session.add(log)
        await session.commit()
        return str(log.id)
    finally:
        await session.close()


@celery_app.task(bind=True, name="workers.reconciliation.tasks.reconcile_document")
def reconcile_document(self, document_id: str, tenant_id: str, priority: int = 5) -> dict[str, Any]:
    """Reconcile document line items and validate totals.

    Args:
        document_id: Document UUID
        tenant_id: Tenant UUID
        priority: Task priority (0=highest, 9=lowest)

    Returns:
        Reconciliation result
    """
    start_time = time.time()
    logger.info(f"Starting reconciliation for document {document_id}")

    try:
        update_document_status(document_id, DocumentStatus.RECONCILIATION)

        import asyncio

        async def _get_structured():
            session = await get_db_session()
            try:
                result = await session.execute(
                    select(StructuredResult).where(
                        StructuredResult.document_id == uuid.UUID(document_id)
                    )
                )
                return result.scalar_one_or_none()
            finally:
                await session.close()

        structured = asyncio.run(_get_structured())
        if not structured:
            logger.info(f"No structured result for document {document_id}, skipping reconciliation")
            update_document_status(document_id, DocumentStatus.VALIDATING)
            from workers.validation.tasks import validate_document
            validate_document.apply_async(
                args=[document_id, tenant_id, priority],
                priority=priority,
            )
            return {
                "document_id": document_id,
                "status": "skipped",
                "reason": "No structured data or line items",
            }

        reconciliation = reconcile_line_items(structured.extracted_data)

        processing_time_ms = int((time.time() - start_time) * 1000)

        asyncio.run(save_reconciliation_log(
            document_id=document_id,
            reconciliation=reconciliation,
            processing_time_ms=processing_time_ms,
        ))

        update_document_status(document_id, DocumentStatus.VALIDATING)
        from workers.validation.tasks import validate_document
        validate_document.apply_async(
            args=[document_id, tenant_id, priority],
            priority=priority,
        )

        processing_time = time.time() - start_time

        logger.info(
            f"Reconciliation completed for document {document_id}: "
            f"status={reconciliation['status']}"
        )

        return {
            "document_id": document_id,
            "status": reconciliation["status"],
            "line_items_count": reconciliation["line_items_count"],
            "subtotal_match": reconciliation["subtotal_match"],
            "vat_match": reconciliation["vat_match"],
            "total_match": reconciliation["total_match"],
            "discrepancies": reconciliation.get("discrepancy_details"),
            "processing_time": processing_time,
        }

    except Exception as exc:
        logger.exception(f"Reconciliation failed for document {document_id}")
        update_document_status(
            document_id,
            DocumentStatus.RECONCILIATION_FAILED,
            error_message=str(exc),
        )

        raise self.retry(exc=exc, countdown=60, max_retries=3)
