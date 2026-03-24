"""Authentication and authorization API routes."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.core.database import get_db
from api.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_tenant,
)
from api.models.db import APIKey, Tenant
from api.routes.schemas import (
    APIKeyCreatedResponse,
    APIKeyCreate,
    APIKeyResponse,
    LoginRequest,
    TenantCreate,
    TenantResponse,
    TenantSettingsUpdate,
    Token,
    TokenRefresh,
)

router = APIRouter()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def hash_password(password: str) -> str:
    """Hash a password."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.
    
    Returns:
        Tuple of (full_key, key_prefix, key_hash)
    """
    full_key = f"ocr_{secrets.token_urlsafe(32)}"
    key_prefix = full_key[:20]
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, key_prefix, key_hash


@router.post("/register", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def register_tenant(
    data: TenantCreate,
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    """Register a new tenant."""
    existing = await db.execute(
        select(Tenant).where(Tenant.email == data.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    slug = data.email.split("@")[0].lower().replace(".", "-").replace("+", "-")
    base_slug = slug
    counter = 1
    while True:
        existing = await db.execute(select(Tenant).where(Tenant.slug == slug))
        if not existing.scalar_one_or_none():
            break
        slug = f"{base_slug}-{counter}"
        counter += 1

    tenant = Tenant(
        name=data.name,
        slug=slug,
        email=data.email,
        password_hash=hash_password(data.password),
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    return TenantResponse.model_validate(tenant)


@router.post("/login", response_model=Token)
async def login(
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Authenticate a tenant and return tokens."""
    result = await db.execute(
        select(Tenant).where(Tenant.email == data.email)
    )
    tenant = result.scalar_one_or_none()

    if not tenant or not verify_password(data.password, tenant.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    access_token = create_access_token({"sub": str(tenant.id), "type": "access"})
    refresh_token = create_refresh_token({"sub": str(tenant.id), "type": "refresh"})

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=Token)
async def refresh_tokens(
    data: TokenRefresh,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Refresh access and refresh tokens."""
    payload = decode_token(data.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    tenant_id = payload.get("sub")
    result = await db.execute(
        select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
    )
    tenant = result.scalar_one_or_none()

    if not tenant or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant not found or inactive",
        )

    access_token = create_access_token({"sub": str(tenant.id), "type": "access"})
    refresh_token = create_refresh_token({"sub": str(tenant.id), "type": "refresh"})

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/api-keys", response_model=APIKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    data: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> APIKeyCreatedResponse:
    """Create a new API key for programmatic access."""
    full_key, key_prefix, key_hash = generate_api_key()

    expires_at = None
    if data.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=data.expires_in_days)

    api_key = APIKey(
        tenant_id=tenant_id,
        name=data.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return APIKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key=full_key,
        key_prefix=api_key.key_prefix,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
    )


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> list[APIKeyResponse]:
    """List all API keys for the current tenant."""
    result = await db.execute(
        select(APIKey)
        .where(APIKey.tenant_id == tenant_id)
        .order_by(APIKey.created_at.desc())
    )
    keys = result.scalars().all()

    return [APIKeyResponse.model_validate(key) for key in keys]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> None:
    """Delete an API key."""
    result = await db.execute(
        select(APIKey).where(
            APIKey.id == key_id,
            APIKey.tenant_id == tenant_id,
        )
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    await db.delete(api_key)
    await db.commit()


@router.get("/me", response_model=TenantResponse)
async def get_current_user(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    """Get the current authenticated tenant."""
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    return TenantResponse.model_validate(tenant)


@router.patch("/me/settings", response_model=TenantResponse)
async def update_tenant_settings(
    data: TenantSettingsUpdate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    """Update tenant settings (e.g. set plan to 'enterprise' for priority lane)."""
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Merge incoming settings with existing ones
    current = dict(tenant.settings or {})
    current.update(data.settings)
    tenant.settings = current

    await db.commit()
    await db.refresh(tenant)

    return TenantResponse.model_validate(tenant)
