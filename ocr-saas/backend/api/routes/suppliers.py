"""Supplier registry API routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.core.security import get_current_tenant
from api.models.db import Supplier
from api.routes.schemas import (
    SupplierCreate,
    SupplierListResponse,
    SupplierResponse,
    SupplierUpdate,
)

router = APIRouter()


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
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> SupplierResponse:
    """Create a new supplier. PIB must be unique per tenant."""
    if data.pib:
        existing = await db.execute(
            select(Supplier).where(
                Supplier.tenant_id == tenant_id,
                Supplier.pib == data.pib,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Supplier with PIB {data.pib} already exists for this tenant",
            )

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
    tenant_id: uuid.UUID = Depends(get_current_tenant),
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
        existing = await db.execute(
            select(Supplier).where(
                Supplier.tenant_id == tenant_id,
                Supplier.pib == data.pib,
                Supplier.id != supplier_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Supplier with PIB {data.pib} already exists for this tenant",
            )

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

    supplier.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(supplier)

    return SupplierResponse.model_validate(supplier)
