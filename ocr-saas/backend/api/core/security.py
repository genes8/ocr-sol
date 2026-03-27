"""Security utilities - JWT, password hashing, API keys."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.core.database import get_db
from api.models.db import APIKey, Tenant

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def create_refresh_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT refresh token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def decode_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        return None


# API Key utilities
def generate_api_key() -> tuple[str, str]:
    """Generate a new API key and its hash.

    Returns:
        Tuple of (plain_key, hashed_key)
    """
    plain_key = f"ocr_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
    key_prefix = plain_key[:12]
    return plain_key, key_hash, key_prefix


def hash_api_key(plain_key: str) -> str:
    """Hash an API key."""
    return hashlib.sha256(plain_key.encode()).hexdigest()


def verify_api_key(plain_key: str, hashed_key: str) -> bool:
    """Verify an API key against its hash."""
    return hash_api_key(plain_key) == hashed_key


async def _resolve_auth(
    authorization: str | None,
    api_key_header: str | None,
    db: AsyncSession,
) -> tuple[uuid.UUID, str]:
    """Resolve auth credentials → (tenant_id, role).

    JWT tokens (tenant owner login) are always "admin" role.
    API keys carry their stored role ("admin" | "reviewer" | "readonly").
    """
    if api_key_header:
        key_hash = hash_api_key(api_key_header)
        result = await db.execute(
            select(APIKey)
            .join(APIKey.tenant)
            .where(APIKey.key_hash == key_hash)
            .where(APIKey.is_active.is_(True))
            .where(Tenant.is_active.is_(True))
        )
        api_key_obj = result.scalar_one_or_none()
        if api_key_obj:
            if api_key_obj.expires_at and api_key_obj.expires_at < datetime.now(timezone.utc):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key expired")
            return api_key_obj.tenant_id, api_key_obj.role

    if authorization:
        try:
            scheme, token = authorization.split(" ", 1)
            if scheme.lower() == "bearer":
                payload = decode_token(token)
                if payload:
                    tenant_id_str = payload.get("sub")
                    if tenant_id_str:
                        result = await db.execute(
                            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id_str))
                        )
                        tenant = result.scalar_one_or_none()
                        if tenant and tenant.is_active:
                            return tenant.id, "admin"
        except (ValueError, AttributeError):
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_tenant(
    authorization: str | None = Header(None),
    api_key: str | None = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """Get the current tenant from the JWT token or API key."""
    tenant_id, _ = await _resolve_auth(authorization, api_key, db)
    return tenant_id


async def get_current_tenant_and_role(
    authorization: str | None = Header(None),
    api_key: str | None = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> tuple[uuid.UUID, str]:
    """Get (tenant_id, role) from JWT or API key."""
    return await _resolve_auth(authorization, api_key, db)


def require_role(*allowed_roles: str) -> Callable:
    """Dependency factory: allows access only to the specified roles.

    Usage:
        @router.delete("/{id}")
        async def delete_doc(
            tenant_id: uuid.UUID = Depends(require_role("admin")),
        ): ...
    """
    async def _check(
        authorization: str | None = Header(None),
        api_key: str | None = Header(None, alias="X-API-Key"),
        db: AsyncSession = Depends(get_db),
    ) -> uuid.UUID:
        tenant_id, role = await _resolve_auth(authorization, api_key, db)
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' is not allowed. Required: {list(allowed_roles)}",
            )
        return tenant_id
    return Depends(_check)
