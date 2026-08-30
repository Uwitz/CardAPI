from fastapi import HTTPException, Request

from app.database import db
from app.session import read_session


class NotAuthenticated(Exception):
    """Raised when a dashboard route has no valid session."""


class NotAdmin(Exception):
    """Raised when a dashboard admin route is accessed by a non-admin user."""


async def _find_user_by_token(token: str) -> dict | None:
    if not token:
        return None
    return await db["users"].find_one({"token": token})


async def _find_user_by_id(user_id: str) -> dict | None:
    if not user_id:
        return None
    return await db["users"].find_one({"_id": user_id})


async def get_api_user(request: Request) -> dict:
    """Extract Bearer token from Authorization header. For REST API routes."""
    auth = request.headers.get("Authorization", "")
    if not auth:
        raise HTTPException(status_code=401, detail={"error": "token_required"})
    token = auth[7:] if auth.lower().startswith("bearer ") else auth
    user = await _find_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail={"error": "invalid_token"})
    return user


async def get_dashboard_user(request: Request) -> dict:
    """Read signed session cookie. For Jinja2 dashboard routes."""
    session = read_session(request)
    if not session:
        raise NotAuthenticated()
    user = await _find_user_by_id(session.get("user_id", ""))
    if not user:
        raise NotAuthenticated()
    return user


async def get_dashboard_admin(request: Request) -> dict:
    """Like get_dashboard_user but requires is_admin=True."""
    user = await get_dashboard_user(request)
    if not user.get("is_admin"):
        raise NotAdmin()
    return user
