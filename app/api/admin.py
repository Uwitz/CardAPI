import secrets as _secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth import get_dashboard_admin, hash_password
from app.database import db, now_iso
from app.config import get_settings
from app.idgen import gen_user_id, gen_token, gen_referral, gen_short_hex

settings = get_settings()

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users")
async def list_users(user: dict = Depends(get_dashboard_admin)):
    users = await db["users"].find({}, {"password_hash": 0}).sort("created_at", -1).to_list(500)
    return users


@router.post("/users")
async def create_user(request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    if await db["users"].find_one({"email": data.get("email", "")}):
        raise HTTPException(status_code=409, detail={"error": "email_exists"})
    if await db["users"].find_one({"username": data.get("username", "")}):
        raise HTTPException(status_code=409, detail={"error": "username_exists"})
    now = now_iso()
    new_user = {
        "_id": gen_user_id(),
        "username": data["username"],
        "email": data["email"],
        "password_hash": hash_password(data["password"]),
        "display_name": data.get("display_name", data["username"]),
        "role": data.get("role", "individual"),
        "org_id": None,
        "stripe_customer_id": None,
        "token": gen_token(),
        "status": "active",
        "referral_code": gen_referral(),
        "created_at": now,
        "updated_at": now,
    }
    await db["users"].insert_one(new_user)
    return {"id": new_user["_id"]}


@router.get("/users/{user_id}")
async def get_user(user_id: str, user: dict = Depends(get_dashboard_admin)):
    target = await db["users"].find_one({"_id": user_id}, {"password_hash": 0})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    cards = await db["cards"].find({"owner_id": user_id}, {"pin": 0}).to_list(100)
    subs = await db["subscriptions"].find({"user_id": user_id}).to_list(50)
    orders = await db["orders"].find({"user_id": user_id}).sort("created_at", -1).to_list(50)
    return {"user": target, "cards": cards, "subscriptions": subs, "orders": orders}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    target = await db["users"].find_one({"_id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    updates = {}
    for field in ("display_name", "email", "role", "status", "org_id"):
        if field in data and data[field] is not None:
            updates[field] = data[field]
    if updates:
        updates["updated_at"] = now_iso()
        await db["users"].update_one({"_id": user_id}, {"$set": updates})
    return {"status": "updated"}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, user: dict = Depends(get_dashboard_admin)):
    if user_id == user["_id"]:
        raise HTTPException(status_code=400, detail={"error": "cannot_delete_self"})
    await db["cards"].delete_many({"owner_id": user_id})
    await db["orders"].delete_many({"user_id": user_id})
    await db["subscriptions"].delete_many({"user_id": user_id})
    await db["users"].delete_one({"_id": user_id})
    return {"status": "deleted"}


@router.post("/users/{user_id}/reset-password")
async def reset_password(user_id: str, user: dict = Depends(get_dashboard_admin)):
    target = await db["users"].find_one({"_id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    new_password = _secrets.token_urlsafe(12)
    await db["users"].update_one(
        {"_id": user_id},
        {"$set": {"password_hash": hash_password(new_password), "updated_at": now_iso()}},
    )
    from app.email import send_email
    await send_email(target["email"], "Your password has been reset", "password_reset", {
        "display_name": target.get("display_name", ""),
        "reset_code": new_password,
    })
    return {"status": "reset", "new_password": new_password}


@router.post("/users/{user_id}/invite")
async def invite_user(user_id: str, user: dict = Depends(get_dashboard_admin)):
    target = await db["users"].find_one({"_id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if target.get("password_hash"):
        return {"status": "already_has_password"}

    new_password = _secrets.token_urlsafe(12)
    await db["users"].update_one(
        {"_id": user_id},
        {"$set": {"password_hash": hash_password(new_password), "updated_at": now_iso()}},
    )
    from app.email import send_email
    await send_email(target["email"], "Your Uwitz Cards account is ready", "corp_invite", {
        "display_name": target.get("display_name", target.get("username", "")),
        "org_name": "Uwitz Cards",
        "email": target["email"],
        "temp_password": new_password,
        "site_url": settings.SITE_URL,
    })
    return {"status": "invited", "temp_password": new_password}


@router.post("/users/{user_id}/renew")
async def renew_user(user_id: str, user: dict = Depends(get_dashboard_admin)):
    from datetime import datetime, timedelta, timezone
    now = now_iso()
    end = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    result = await db["subscriptions"].update_many(
        {"user_id": user_id, "status": {"$in": ["active", "past_due"]}},
        {"$set": {"status": "active", "current_period_end": end, "expires_at": end, "updated_at": now}},
    )
    async for sub in db["subscriptions"].find({"user_id": user_id}):
        await db["cards"].update_one({"_id": sub["card_id"]}, {"$set": {"subscription_status": "active"}})
    return {"status": "renewed", "subscriptions_updated": result.modified_count}


@router.post("/users/{user_id}/exempt")
async def exempt_user(user_id: str, user: dict = Depends(get_dashboard_admin)):
    await db["subscriptions"].update_many({"user_id": user_id}, {"$set": {"status": "cancelled", "updated_at": now_iso()}})
    await db["cards"].update_many({"owner_id": user_id}, {"$set": {"subscription_status": "active"}})
    return {"status": "exempted"}


# --- Cards ---

@router.get("/cards")
async def list_all_cards(user: dict = Depends(get_dashboard_admin)):
    cards = await db["cards"].find({}, {"pin": 0}).sort("created_at", -1).to_list(500)
    return cards


@router.get("/cards/{card_id}")
async def get_card(card_id: str, user: dict = Depends(get_dashboard_admin)):
    card = await db["cards"].find_one({"card_id": card_id}, {"pin": 0})
    if not card:
        card = await db["cards"].find_one({"_id": card_id}, {"pin": 0})
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    owner = await db["users"].find_one({"_id": card.get("owner_id")}, {"password_hash": 0}) if card.get("owner_id") else None
    image = await db["card_images"].find_one({"card_id": card["_id"]})
    return {"card": card, "owner": owner, "image": image}


@router.patch("/cards/{card_id}")
async def admin_update_card(card_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    card = await db["cards"].find_one({"card_id": card_id})
    if not card:
        card = await db["cards"].find_one({"_id": card_id})
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    updates = {}
    for field in ("status", "frozen", "freeze_reason", "card_type", "card_tier", "vcard_data", "redirect_url", "plain_text", "owner_id"):
        if field in data:
            updates[field] = data[field]
    if updates:
        updates["updated_at"] = now_iso()
        await db["cards"].update_one({"_id": card["_id"]}, {"$set": updates})
    return {"status": "updated"}


@router.delete("/cards/{card_id}")
async def admin_delete_card(card_id: str, user: dict = Depends(get_dashboard_admin)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card:
        card = await db["cards"].find_one({"_id": card_id})
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    await db["cards"].delete_one({"_id": card["_id"]})
    await db["taglink_api_keys"].delete_many({"card_id": card["_id"]})
    return {"status": "deleted"}


# --- Orders ---

@router.get("/orders")
async def list_all_orders(user: dict = Depends(get_dashboard_admin)):
    return await db["orders"].find({}).sort("created_at", -1).to_list(500)


@router.patch("/orders/{order_id}")
async def update_order(order_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    updates = {k: v for k, v in data.items() if k in ("status", "notes")}
    if updates:
        updates["updated_at"] = now_iso()
        await db["orders"].update_one({"_id": order_id}, {"$set": updates})
    return {"status": "updated"}


# --- Subscriptions ---

@router.get("/subscriptions")
async def list_all_subscriptions(user: dict = Depends(get_dashboard_admin)):
    return await db["subscriptions"].find({}).sort("created_at", -1).to_list(500)


@router.patch("/subscriptions/{sub_id}")
async def update_subscription(sub_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    updates = {k: v for k, v in data.items() if k in ("status", "expires_at", "current_period_end")}
    if updates:
        updates["updated_at"] = now_iso()
        await db["subscriptions"].update_one({"_id": sub_id}, {"$set": updates})
    return {"status": "updated"}


# --- Orphans ---

@router.get("/orphans")
async def list_orphan_cards(user: dict = Depends(get_dashboard_admin)):
    user_ids = set()
    async for u in db["users"].find({}, {"_id": 1}):
        user_ids.add(u["_id"])
    orphans = []
    async for card in db["cards"].find({}, {"pin": 0}):
        owner = card.get("owner_id", "")
        if not owner or owner not in user_ids:
            orphans.append(card)
    async for card in db["user_cards"].find({}, {"pin": 0}):
        owner = card.get("owner_id", "")
        if not owner or owner not in user_ids:
            orphans.append({"_id": card["_id"], "card_id": card.get("_id", ""), "owner_id": owner, "card_type": card.get("type", "vcard"), "card_tier": card.get("tier", "plastic"), "status": card.get("status", "active"), "views": card.get("views", 0), "created_at": card.get("created_at", ""), "_legacy": True})
    return orphans


@router.post("/orphans/{card_id}/assign")
async def assign_orphan_card(card_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    target_user_id = data.get("user_id")
    if not target_user_id:
        raise HTTPException(status_code=400, detail={"error": "user_id_required"})
    target_user = await db["users"].find_one({"_id": target_user_id})
    if not target_user:
        raise HTTPException(status_code=404, detail={"error": "user_not_found"})
    now = now_iso()
    await db["cards"].update_one({"card_id": card_id}, {"$set": {"owner_id": target_user_id, "org_id": target_user.get("org_id"), "updated_at": now}})
    await db["user_cards"].update_one({"_id": card_id}, {"$set": {"owner_id": target_user_id, "updated_at": now}})
    return {"status": "assigned"}


@router.post("/orphans/{card_id}/invite")
async def invite_orphan_owner(card_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    email = data.get("email", "").strip()
    if not email:
        raise HTTPException(status_code=400, detail={"error": "email_required"})
    now = now_iso()
    existing = await db["users"].find_one({"email": email})
    if existing:
        await db["cards"].update_one({"card_id": card_id}, {"$set": {"owner_id": existing["_id"], "updated_at": now}})
        await db["user_cards"].update_one({"_id": card_id}, {"$set": {"owner_id": existing["_id"], "updated_at": now}})
        return {"status": "assigned_to_existing"}
    temp_password = _secrets.token_urlsafe(12)
    username = email.split("@")[0].lower()
    base = username
    counter = 1
    while await db["users"].find_one({"username": username}):
        username = f"{base}{counter}"
        counter += 1
    user_id = gen_user_id()
    await db["users"].insert_one({"_id": user_id, "username": username, "email": email, "password_hash": hash_password(temp_password), "display_name": username, "role": "individual", "org_id": None, "stripe_customer_id": None, "token": gen_token(), "status": "active", "referral_code": gen_referral(), "created_at": now, "updated_at": now})
    await db["cards"].update_one({"card_id": card_id}, {"$set": {"owner_id": user_id, "updated_at": now}})
    await db["user_cards"].update_one({"_id": card_id}, {"$set": {"owner_id": user_id, "updated_at": now}})
    from app.email import send_email
    await send_email(email, "Your Uwitz Cards account is ready", "corp_invite", {"display_name": username, "org_name": "Uwitz Cards", "email": email, "temp_password": temp_password, "site_url": settings.SITE_URL})
    return {"status": "invited", "temp_password": temp_password}


# --- Stats ---

@router.get("/stats")
async def admin_stats(user: dict = Depends(get_dashboard_admin)):
    users = await db["users"].count_documents({})
    cards = await db["cards"].count_documents({})
    orders = await db["orders"].count_documents({})
    active_subs = await db["subscriptions"].count_documents({"status": "active"})
    pipeline = [{"$match": {"status": "paid"}}, {"$group": {"_id": None, "total": {"$sum": "$amount_total"}}}]
    result = await db["orders"].aggregate(pipeline).to_list(1)
    return {"users": users, "cards": cards, "orders": orders, "active_subscriptions": active_subs, "total_revenue": result[0]["total"] if result else 0}


@router.get("/pricing")
async def get_pricing(user: dict = Depends(get_dashboard_admin)):
    from app.pricing import CARD_PRICING, SUBSCRIPTION_PRICING
    return {"card_pricing": CARD_PRICING, "subscription_pricing": SUBSCRIPTION_PRICING}


@router.get("/email-logs")
async def list_email_logs(user: dict = Depends(get_dashboard_admin)):
    return await db["email_logs"].find({}).sort("created_at", -1).to_list(200)
