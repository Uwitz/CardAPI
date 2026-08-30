import re

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_api_user
from app.database import db, now_iso

router = APIRouter(prefix="/api/admin", tags=["admin"])

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
VCARD_RE = re.compile(r"^BEGIN:VCARD.*END:VCARD\s*$", re.DOTALL)
URL_RE = re.compile(r"^https?://.+")


def _require_admin(user: dict) -> None:
    if not user.get("is_admin"):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})


def _user_to_response(user: dict) -> dict:
    return {
        "id": user.get("_id"),
        "display_name": user.get("display_name"),
        "email": user.get("email"),
        "plan_expiry": user.get("plan_expiry"),
        "referral": user.get("referral"),
        "referral_reward": user.get("referral_reward", 0.0),
        "currency": user.get("currency", "MYR"),
        "payouts": user.get("payouts", []),
        "username": user.get("username"),
        "is_admin": user.get("is_admin", False),
        "plan": user.get("plan"),
        "organisation": user.get("organisation"),
        "status": user.get("status"),
        "transactions": user.get("transactions", []),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


def _card_to_response(card: dict) -> dict:
    return {
        "id": card.get("_id"),
        "tier": card.get("tier"),
        "owner_id": card.get("owner_id"),
        "type": card.get("type"),
        "content": card.get("content"),
        "payment_id": card.get("payment_id"),
        "organisation": card.get("organisation"),
        "views": card.get("views", 0),
        "status": card.get("status"),
        "version": card.get("version"),
        "created_at": card.get("created_at"),
        "updated_at": card.get("updated_at"),
    }


# --- Users ---

@router.get("/users")
async def list_users(page: int = 1, limit: int = 50, user: dict = Depends(get_api_user)):
    _require_admin(user)
    skip = (page - 1) * limit
    users = [_user_to_response(u) async for u in db["users"].find({}).skip(skip).limit(limit)]
    total = await db["users"].count_documents({})
    return {"users": users, "page": page, "limit": limit, "total": total}


@router.post("/users")
async def create_user(request: Request, user: dict = Depends(get_api_user)):
    _require_admin(user)
    import binascii
    import datetime as _dt
    import os
    import random
    import string

    body = await request.json()
    username = str(body.get("username", "")).strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail={"error": "invalid_username"})

    if await db["users"].find_one({"username": username}):
        raise HTTPException(status_code=409, detail={"error": "duplicate_username"})

    email = body.get("email")
    if not email:
        raise HTTPException(status_code=400, detail={"error": "email_missing"})

    plan = body.get("plan", "individual")
    plan_expiry = None if plan == "individual" else str(
        int(_dt.datetime.now(_dt.timezone.utc).timestamp()) + 30 * 24 * 60 * 60
    )

    new_user = {
        "_id": "".join(random.choices(string.digits, k=10)) + "." + str(int(_dt.datetime.now().timestamp())),
        "username": username,
        "display_name": body.get("display_name", username),
        "email": email.strip().lower(),
        "plan_expiry": plan_expiry,
        "referral": "".join(random.choices(string.ascii_uppercase + string.digits, k=6)),
        "referral_reward": 0,
        "currency": body.get("currency", "MYR"),
        "payouts": [],
        "token": binascii.hexlify(os.urandom(20)).decode(),
        "is_admin": body.get("is_admin", False),
        "plan": plan,
        "organisation": body.get("organisation"),
        "status": "active",
        "transactions": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db["users"].insert_one(new_user)
    resp = _user_to_response(new_user)
    resp["token"] = new_user["token"]  # only returned on creation
    return resp


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, user: dict = Depends(get_api_user)):
    """Admin terminates a user and deletes all their cards. Any admin can call."""
    _require_admin(user)
    await db["users"].delete_one({"_id": user_id})
    await db["user_cards"].delete_many({"owner_id": user_id})
    return {"status": "success"}


@router.post("/users/{user_id}/renew")
async def renew_user(user_id: str, request: Request, user: dict = Depends(get_api_user)):
    _require_admin(user)
    import datetime as _dt
    import random
    import string

    body = await request.json()
    updates = {}
    if body.get("plan"):
        updates["plan"] = body["plan"]
    if "plan_expiry" in body:
        updates["plan_expiry"] = body["plan_expiry"]
    elif updates.get("plan") and updates["plan"] != "individual":
        updates["plan_expiry"] = str(
            int(_dt.datetime.now(_dt.timezone.utc).timestamp()) + 30 * 24 * 60 * 60
        )

    transaction = body.get("transaction")
    update_ops = {"$set": {**updates, "updated_at": now_iso()}}
    if isinstance(transaction, dict):
        trans_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
        update_ops["$push"] = {
            "transactions": {
                "type": transaction.get("type"),
                "id": trans_id,
                "bank": transaction.get("bank"),
                "gateway": transaction.get("gateway"),
                "reference": transaction.get("reference"),
                "amount": transaction.get("amount"),
                "timestamp": transaction.get("timestamp") or now_iso(),
                "referral": transaction.get("referral"),
            }
        }

    result = await db["users"].update_one({"_id": user_id}, update_ops)
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    target = await db["users"].find_one({"_id": user_id})
    return _user_to_response(target)


# --- Cards ---

@router.get("/cards")
async def list_all_cards(
    page: int = 1,
    limit: int = 50,
    status: str = "",
    type: str = "",
    user: dict = Depends(get_api_user),
):
    _require_admin(user)
    skip = (page - 1) * limit
    query = {}
    if status:
        query["status"] = status
    if type:
        query["type"] = type
    cards = [_card_to_response(c) async for c in db["user_cards"].find(query).skip(skip).limit(limit)]
    total = await db["user_cards"].count_documents(query)
    return {"cards": cards, "page": page, "limit": limit, "total": total}


@router.post("/cards")
async def admin_create_card(request: Request, user: dict = Depends(get_api_user)):
    _require_admin(user)
    import random
    import string
    import datetime as _dt

    body = await request.json()
    card_type = body.get("type")
    content = body.get("content")
    owner_id = body.get("owner_id")

    if card_type not in ("vcard", "url"):
        raise HTTPException(status_code=400, detail={"error": "invalid_type"})

    if card_type == "vcard" and (not content or not VCARD_RE.match(content)):
        raise HTTPException(status_code=400, detail={"error": "invalid_format"})
    if card_type == "url" and (not content or not URL_RE.match(content)):
        raise HTTPException(status_code=400, detail={"error": "invalid_url"})

    owner = await db["users"].find_one({"_id": owner_id})
    if not owner:
        raise HTTPException(status_code=400, detail={"error": "invalid_owner_id"})

    payload = {
        "_id": "".join(random.choices(string.ascii_letters + string.digits, k=8)),
        "tier": body.get("tier", "plastic"),
        "owner_id": owner_id,
        "type": card_type,
        "content": content,
        "payment_id": body.get("payment_id"),
        "organisation": owner.get("organisation"),
        "views": 0,
        "status": body.get("status", "active"),
        "version": 1.0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db["user_cards"].insert_one(payload)
    return _card_to_response(payload)
