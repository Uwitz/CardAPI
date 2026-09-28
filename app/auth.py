import bcrypt
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Request

from app.database import db
from app.session import read_session


class NotAuthenticated(Exception):
    pass


class NotAdmin(Exception):
    pass


class NotCorporateAdmin(Exception):
    pass


class NotLogisticsAdmin(Exception):
    pass


class NotPendingUser(Exception):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def gen_activation_token() -> str:
    """Generate cryptographically secure activation token."""
    return secrets.token_urlsafe(32)


import hashlib


async def _hash_token(token: str) -> str:
    """Hash token for storage using SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_activation_token(card_id: str, expires_days: int = 7) -> dict:
    """Create and store activation token for a card."""
    token = gen_activation_token()
    token_hash = await _hash_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
    await db["cards"].update_one(
        {"card_id": card_id},
        {"$set": {
            "activation_token_hash": token_hash,
            "activation_expires_at": expires_at.isoformat(),
            "activation_used": False,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    return {"token": token, "expires_at": expires_at}


async def verify_activation_token(card_id: str, token: str) -> tuple[bool, dict | None]:
    """Verify activation token for a card. Returns (is_valid, card_doc)."""
    card = await db["cards"].find_one({"card_id": card_id})
    if not card:
        return False, None
    
    stored_hash = card.get("activation_token_hash")
    expires_at_str = card.get("activation_expires_at")
    used = card.get("activation_used", False)
    
    if not stored_hash:
        return False, None
    
    token_hash = await _hash_token(token)
    if not hmac.compare_digest(stored_hash, token_hash):
        return False, None
    
    if used:
        return False, None
    
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) > expires_at:
                return False, None
        except ValueError:
            return False, None
    
    return True, card


async def mark_activation_used(card_id: str) -> None:
    """Mark activation token as used."""
    await db["cards"].update_one(
        {"card_id": card_id},
        {"$set": {"activation_used": True, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )


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
    # Allow pending users but they'll have limited access
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


async def get_dashboard_logistics_admin(request: Request) -> dict:
    user = await get_dashboard_user(request)
    if user.get("role") not in ("admin", "logistics_admin"):
        raise NotLogisticsAdmin()
    return user


async def get_activation_user(request: Request) -> dict:
    """Get user for activation flow - allows pending users with valid token."""
    # Check for activation token in query params or session
    card_id = request.query_params.get("card_id") or request.path_params.get("card_id")
    token = request.query_params.get("token")
    
    if not card_id or not token:
        raise NotAuthenticated()
    
    valid, card = await verify_activation_token(card_id, token)
    if not valid or not card:
        raise NotAuthenticated()
    
    owner_id = card.get("owner_id")
    if not owner_id:
        raise NotAuthenticated()
    
    user = await db["users"].find_one({"_id": owner_id})
    if not user:
        raise NotAuthenticated()
    
    # Attach card info to user for convenience
    user["_activation_card"] = card
    return user


async def require_active_user(request: Request) -> dict:
    """Require fully active user (not pending)."""
    user = await get_dashboard_user(request)
    if user.get("status") == "pending":
        raise NotPendingUser()
    return user
