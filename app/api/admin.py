from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.auth import get_dashboard_admin, hash_password
from app.database import db, now_iso
from app.models import AdminUserCreate, AdminUserUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])


# --- Users ---

@router.get("/users")
async def list_users(user: dict = Depends(get_dashboard_admin)):
    users = await db["users"].find({}, {"password_hash": 0}).sort("created_at", -1).to_list(500)
    return users


@router.post("/users")
async def create_user(data: AdminUserCreate, user: dict = Depends(get_dashboard_admin)):
    import secrets
    if await db["users"].find_one({"email": data.email}):
        raise HTTPException(status_code=409, detail={"error": "email_exists"})
    if await db["users"].find_one({"username": data.username}):
        raise HTTPException(status_code=409, detail={"error": "username_exists"})

    now = now_iso()
    new_user = {
        "_id": secrets.token_hex(5),
        "username": data.username,
        "email": data.email,
        "password_hash": hash_password(data.password),
        "display_name": data.display_name,
        "role": data.role,
        "org_id": None,
        "stripe_customer_id": None,
        "token": secrets.token_hex(20),
        "status": "active",
        "referral_code": secrets.token_hex(3).upper(),
        "created_at": now,
        "updated_at": now,
    }
    await db["users"].insert_one(new_user)
    return {"id": new_user["_id"], "token": new_user["token"]}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, data: AdminUserUpdate, user: dict = Depends(get_dashboard_admin)):
    target = await db["users"].find_one({"_id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    updates = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
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


@router.post("/users/{user_id}/renew")
async def renew_user(user_id: str, user: dict = Depends(get_dashboard_admin)):
    from datetime import datetime, timedelta, timezone
    target = await db["users"].find_one({"_id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    now = now_iso()
    end = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()

    # Update all active subscriptions for this user
    result = await db["subscriptions"].update_many(
        {"user_id": user_id, "status": {"$in": ["active", "past_due"]}},
        {"$set": {"status": "active", "current_period_end": end, "expires_at": end, "updated_at": now}},
    )

    # Update associated cards
    async for sub in db["subscriptions"].find({"user_id": user_id}):
        await db["cards"].update_one(
            {"_id": sub["card_id"]},
            {"$set": {"subscription_status": "active"}},
        )

    return {"status": "renewed", "subscriptions_updated": result.modified_count}


@router.post("/users/{user_id}/exempt")
async def exempt_user(user_id: str, user: dict = Depends(get_dashboard_admin)):
    target = await db["users"].find_one({"_id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    # Cancel all subscriptions and keep cards active
    await db["subscriptions"].update_many(
        {"user_id": user_id},
        {"$set": {"status": "cancelled", "updated_at": now_iso()}},
    )
    await db["cards"].update_many(
        {"owner_id": user_id},
        {"$set": {"subscription_status": "active"}},
    )
    return {"status": "exempted"}


# --- Cards ---

@router.get("/cards")
async def list_all_cards(user: dict = Depends(get_dashboard_admin)):
    cards = await db["cards"].find({}, {"pin": 0}).sort("created_at", -1).to_list(500)
    return cards


@router.patch("/cards/{card_id}")
async def admin_update_card(card_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    updates = {}
    for field in ("status", "frozen", "freeze_reason", "card_type", "card_tier"):
        if field in data:
            updates[field] = data[field]
    if updates:
        updates["updated_at"] = now_iso()
        await db["cards"].update_one({"card_id": card_id}, {"$set": updates})
    return {"status": "updated"}


@router.delete("/cards/{card_id}")
async def admin_delete_card(card_id: str, user: dict = Depends(get_dashboard_admin)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    await db["cards"].delete_one({"_id": card["_id"]})
    await db["taglink_api_keys"].delete_many({"card_id": card["_id"]})
    return {"status": "deleted"}


# --- Orders ---

@router.get("/orders")
async def list_all_orders(user: dict = Depends(get_dashboard_admin)):
    orders = await db["orders"].find({}).sort("created_at", -1).to_list(500)
    return orders


@router.patch("/orders/{order_id}")
async def update_order(order_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    updates = {}
    for field in ("status", "notes"):
        if field in data:
            updates[field] = data[field]
    if updates:
        updates["updated_at"] = now_iso()
        await db["orders"].update_one({"_id": order_id}, {"$set": updates})
    return {"status": "updated"}


# --- Subscriptions ---

@router.get("/subscriptions")
async def list_all_subscriptions(user: dict = Depends(get_dashboard_admin)):
    subs = await db["subscriptions"].find({}).sort("created_at", -1).to_list(500)
    return subs


@router.patch("/subscriptions/{sub_id}")
async def update_subscription(sub_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    updates = {}
    for field in ("status", "expires_at", "current_period_end"):
        if field in data:
            updates[field] = data[field]
    if updates:
        updates["updated_at"] = now_iso()
        await db["subscriptions"].update_one({"_id": sub_id}, {"$set": updates})
    return {"status": "updated"}


# --- Stats ---

@router.get("/stats")
async def admin_stats(user: dict = Depends(get_dashboard_admin)):
    users = await db["users"].count_documents({})
    cards = await db["cards"].count_documents({})
    orders = await db["orders"].count_documents({})
    active_subs = await db["subscriptions"].count_documents({"status": "active"})
    revenue_pipeline = [
        {"$match": {"status": "paid"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_total"}}},
    ]
    revenue_result = await db["orders"].aggregate(revenue_pipeline).to_list(1)
    total_revenue = revenue_result[0]["total"] if revenue_result else 0

    return {
        "users": users,
        "cards": cards,
        "orders": orders,
        "active_subscriptions": active_subs,
        "total_revenue": total_revenue,
    }


# --- Pricing ---

@router.get("/pricing")
async def get_pricing(user: dict = Depends(get_dashboard_admin)):
    from app.pricing import CARD_PRICING, SUBSCRIPTION_PRICING
    return {"card_pricing": CARD_PRICING, "subscription_pricing": SUBSCRIPTION_PRICING}


# --- Email Logs ---

@router.get("/email-logs")
async def list_email_logs(user: dict = Depends(get_dashboard_admin)):
    logs = await db["email_logs"].find({}).sort("created_at", -1).to_list(200)
    return logs
