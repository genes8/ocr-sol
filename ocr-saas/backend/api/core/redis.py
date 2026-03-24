"""Redis connection and caching utilities."""

import json
from typing import Any

import redis.asyncio as redis

from api.core.config import settings

# Global Redis client
redis_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Get Redis client instance."""
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return redis_client


async def close_redis() -> None:
    """Close Redis connection."""
    global redis_client
    if redis_client is not None:
        await redis_client.close()
        redis_client = None


class CacheService:
    """Redis caching service."""

    def __init__(self, client: redis.Redis):
        self.client = client

    async def get(self, key: str) -> Any | None:
        """Get value from cache."""
        value = await self.client.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return None

    async def set(
        self,
        key: str,
        value: Any,
        expire: int | None = None,
    ) -> None:
        """Set value in cache with optional expiration in seconds."""
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        if expire:
            await self.client.setex(key, expire, value)
        else:
            await self.client.set(key, value)

    async def delete(self, key: str) -> None:
        """Delete key from cache."""
        await self.client.delete(key)

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        return await self.client.exists(key) > 0

    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment a counter."""
        return await self.client.incrby(key, amount)

    async def expire(self, key: str, seconds: int) -> None:
        """Set expiration on key."""
        await self.client.expire(key, seconds)


async def get_cache() -> CacheService:
    """Get cache service instance."""
    client = await get_redis()
    return CacheService(client)
