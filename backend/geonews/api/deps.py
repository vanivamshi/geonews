import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geonews.cache import rate_limit
from geonews.config import get_settings
from geonews.db import get_session
from geonews.models import User


async def current_user(
    authorization: str = Header(""), session: AsyncSession = Depends(get_session)
) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "missing token")
    user = (await session.execute(select(User).where(User.token == token))).scalar_one_or_none()
    if user is None:
        raise HTTPException(401, "invalid token")
    if not await rate_limit(f"user:{user.id}", limit=120, window_s=60):
        raise HTTPException(429, "slow down")
    return user


async def require_admin(x_admin_token: str = Header("")) -> None:
    if not secrets.compare_digest(x_admin_token, get_settings().admin_token):
        raise HTTPException(403, "admin only")
