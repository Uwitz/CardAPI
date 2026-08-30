import bcrypt
from fastapi import HTTPException, Request

from app.database import db
from app.session import read_session


class NotAuthenticated(Exception):
    pass


class NotAdmin(Exception):
    pass


class NotCorporateAdmin(Exception):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


async def get_api_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth:
        raise HTTPException(status_code=401, detail={"error": "token_required"})
    token = auth[7:] if auth.lower().startswith("bearer ") else auth
    user = await db["users"].find_one({"token": token})
    if not user:
        raise HTTPException(status_code=401, detail={"error": "invalid_token"})
    return user


async def get_dashboard_user(request: Request) -> dict:
    session = read_session(request)
    if not session:
        raise NotAuthenticated()
    user = await db["users"].find_one({"_id": session.get("user_id", "")})
    if not user:
        raise NotAuthenticated()
    return user


async def get_dashboard_admin(request: Request) -> dict:
    user = await get_dashboard_user(request)
    if user.get("role") != "admin":
        raise NotAdmin()
    return user


async def get_dashboard_corporate_admin(request: Request) -> dict:
    user = await get_dashboard_user(request)
    if user.get("role") not in ("admin", "corporate_admin"):
        raise NotCorporateAdmin()
    return user
