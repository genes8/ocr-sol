"""Supplier registry API routes."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.core.security import get_current_tenant, require_role
from api.models.db import Supplier
from api.routes.schemas import (
    SupplierCreate,
    SupplierListResponse,
    SupplierResponse,
    SupplierUpdate,
)

router = APIRouter()


async def _assert_pib_unique(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pib: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Raise HTTP 409 if another supplier in this tenant already has this PIB."""
    query = select(Supplier).where(
        Supplier.tenant_id == tenant_id,
        Supplier.pib == pib,
    )
    if exclude_id is not None:
        query = query.where(Supplier.id != exclude_id)
    existing = await db.execute(query)
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Supplier with PIB {pib} already exists for this tenant",
        )


@router.get("", response_model=SupplierListResponse)
async def list_suppliers(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> SupplierListResponse:
    """List suppliers for the current tenant."""
    query = select(Supplier).where(Supplier.tenant_id == tenant_id)

    if is_active is not None:
        query = query.where(Supplier.is_active.is_(is_active))

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Supplier.name).offset(skip).limit(limit)
    result = await db.execute(query)
    suppliers = result.scalars().all()

    return SupplierListResponse(
        total=total,
        skip=skip,
        limit=limit,
        items=[SupplierResponse.model_validate(s) for s in suppliers],
    )


@router.post("", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    data: SupplierCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = require_role("admin", "reviewer"),
) -> SupplierResponse:
    """Create a new supplier. PIB must be unique per tenant."""
    if data.pib:
        await _assert_pib_unique(db, tenant_id, data.pib)

    supplier = Supplier(
        tenant_id=tenant_id,
        name=data.name,
        pib=data.pib,
        mb=data.mb,
        iban=data.iban,
        address=data.address,
    )
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)

    return SupplierResponse.model_validate(supplier)


@router.get("/{supplier_id}", response_model=SupplierResponse)
async def get_supplier(
    supplier_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> SupplierResponse:
    """Get a specific supplier by ID."""
    result = await db.execute(
        select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.tenant_id == tenant_id,
        )
    )
    supplier = result.scalar_one_or_none()

    if not supplier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found",
        )

    return SupplierResponse.model_validate(supplier)


@router.patch("/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: uuid.UUID,
    data: SupplierUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = require_role("admin", "reviewer"),
) -> SupplierResponse:
    """Update a supplier."""
    result = await db.execute(
        select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.tenant_id == tenant_id,
        )
    )
    supplier = result.scalar_one_or_none()

    if not supplier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found",
        )

    # Check PIB uniqueness if being changed
    if data.pib is not None and data.pib != supplier.pib:
        await _assert_pib_unique(db, tenant_id, data.pib, exclude_id=supplier_id)

    if data.name is not None:
        supplier.name = data.name
    if data.pib is not None:
        supplier.pib = data.pib
    if data.mb is not None:
        supplier.mb = data.mb
    if data.iban is not None:
        supplier.iban = data.iban
    if data.address is not None:
        supplier.address = data.address
    if data.is_active is not None:
        supplier.is_active = data.is_active

    supplier.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(supplier)

    return SupplierResponse.model_validate(supplier)
