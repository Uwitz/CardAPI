import hashlib
import secrets
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.auth import get_api_user
from app.database import db, now_iso
from app.models import TagLinkUpdate, TagLinkKeyCreate

router = APIRouter(prefix="/api/taglink", tags=["taglink"])

# Simple in-memory rate limiter: card_id → list of timestamps
_rate_limits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 60     # requests per window


def _check_rate_limit(card_id: str) -> bool:
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    _rate_limits[card_id] = [t for t in _rate_limits[card_id] if t > cutoff]
    if len(_rate_limits[card_id]) >= RATE_LIMIT_MAX:
        return False
    _rate_limits[card_id].append(now)
    return True


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _prefix(key: str) -> str:
    return key[:8]


async def _authenticate_taglink(request: Request) -> tuple[dict, dict] | None:
    auth = request.headers.get("Authorization", "")
    if not auth or not auth.lower().startswith("bearer "):
        return None
    key = auth[7:]
    key_hash = _hash_key(key)

    key_doc = await db["taglink_api_keys"].find_one({"key_hash": key_hash, "status": "active"})
    if not key_doc:
        return None

    card = await db["cards"].find_one({"_id": key_doc["card_id"]})
    if not card:
        return None

    await db["taglink_api_keys"].update_one(
        {"_id": key_doc["_id"]},
        {"$set": {"last_used_at": now_iso()}},
    )
    return card, key_doc


# --- External API (card API key auth) ---

@router.post("/{card_id}/update")
async def taglink_update(card_id: str, data: TagLinkUpdate, request: Request):
    auth_result = await _authenticate_taglink(request)
    if not auth_result:
        raise HTTPException(status_code=401, detail={"error": "invalid_api_key"})

    card, key_doc = auth_result
    if card["card_id"] != card_id:
        raise HTTPException(status_code=403, detail={"error": "key_not_for_this_card"})

    if "write" not in key_doc.get("permissions", []):
        raise HTTPException(status_code=403, detail={"error": "no_write_permission"})

    if not _check_rate_limit(card_id):
        raise HTTPException(status_code=429, detail={"error": "rate_limit_exceeded"})

    content = data.content.strip()
    if content.startswith("BEGIN:VCARD"):
        raise HTTPException(status_code=400, detail={"error": "contact_info_not_allowed"})

    updates = {"updated_at": now_iso()}
    if content.startswith("http://") or content.startswith("https://"):
        updates["redirect_url"] = content
        updates["plain_text"] = None
    else:
        updates["plain_text"] = content
        updates["redirect_url"] = None

    await db["cards"].update_one({"_id": card["_id"]}, {"$set": updates})
    return {"status": "updated"}


@router.get("/{card_id}/status")
async def taglink_status(card_id: str, request: Request):
    auth_result = await _authenticate_taglink(request)
    if not auth_result:
        raise HTTPException(status_code=401, detail={"error": "invalid_api_key"})

    card, _ = auth_result
    if card["card_id"] != card_id:
        raise HTTPException(status_code=403, detail={"error": "key_not_for_this_card"})

    return {
        "card_id": card_id,
        "redirect_url": card.get("redirect_url"),
        "plain_text": card.get("plain_text"),
        "frozen": card.get("frozen", False),
        "status": card.get("status"),
    }


# --- Dashboard API (user session auth) ---

@router.get("/{card_id}/keys")
async def list_keys(card_id: str, user: dict = Depends(get_api_user)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    keys = await db["taglink_api_keys"].find(
        {"card_id": card["_id"]},
        {"key_hash": 0},
    ).to_list(50)
    return keys


@router.post("/{card_id}/keys")
async def create_key(card_id: str, data: TagLinkKeyCreate, user: dict = Depends(get_api_user)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    raw_key = f"tl_{secrets.token_hex(24)}"
    key_hash = _hash_key(raw_key)
    now = now_iso()

    key_doc = {
        "_id": secrets.token_hex(8),
        "card_id": card["_id"],
        "key_hash": key_hash,
        "key_prefix": _prefix(raw_key),
        "name": data.name,
        "permissions": ["read", "write"],
        "status": "active",
        "last_used_at": None,
        "created_at": now,
        "revoked_at": None,
    }
    await db["taglink_api_keys"].insert_one(key_doc)

    from app.email import send_email
    owner = await db["users"].find_one({"_id": user["_id"]})
    if owner:
        await send_email(owner["email"], "TagLink API Key Created", "apikey_created", {
            "display_name": owner.get("display_name", ""),
            "card_id": card_id,
            "key_name": data.name,
            "key_prefix": _prefix(raw_key),
        })

    return {"key_id": key_doc["_id"], "key": raw_key, "name": data.name, "prefix": _prefix(raw_key)}


@router.delete("/{card_id}/keys/{key_id}")
async def revoke_key(card_id: str, key_id: str, user: dict = Depends(get_api_user)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    result = await db["taglink_api_keys"].update_one(
        {"_id": key_id, "card_id": card["_id"]},
        {"$set": {"status": "revoked", "revoked_at": now_iso()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail={"error": "key_not_found"})
    return {"status": "revoked"}
