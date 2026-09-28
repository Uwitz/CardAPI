from fastapi import APIRouter, Depends, HTTPException
import stripe

from app.auth import get_api_user
from app.config import get_settings
from app.database import db, now_iso
from app.models import SubscriptionCreate
from app.pricing import SUBSCRIPTION_PRICING

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])
settings = get_settings()

if settings.STRIPE_SECRET_KEY:
    stripe.api_key = settings.STRIPE_SECRET_KEY

PRICE_MAP = {
    "social_yearly": "STRIPE_PRICE_SOCIAL_YEARLY",
    "corporate_yearly": "STRIPE_PRICE_CORPORATE_YEARLY",
    "taglink_monthly": "STRIPE_PRICE_TAGLINK_MONTHLY",
}


@router.get("")
async def list_subscriptions(user: dict = Depends(get_api_user)):
    subs = await db["subscriptions"].find(
        {"user_id": user["_id"]}
    ).sort("created_at", -1).to_list(100)
    return subs


@router.post("")
async def create_subscription(data: SubscriptionCreate, user: dict = Depends(get_api_user)):
    plan_info = SUBSCRIPTION_PRICING.get(data.plan)
    if not plan_info:
        raise HTTPException(status_code=400, detail={"error": "invalid_plan"})

    card = await db["cards"].find_one({"_id": data.card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    now = now_iso()
    sub_id = f"SUB-{now.timestamp():.0f}"

    sub_doc = {
        "_id": sub_id,
        "user_id": user["_id"],
        "org_id": user.get("org_id"),
        "card_id": data.card_id,
        "plan": data.plan,
        "amount": plan_info["amount"],
        "interval": plan_info["interval"],
        "stripe_subscription_id": None,
        "stripe_price_id": None,
        "status": "active",
        "current_period_start": now,
        "current_period_end": None,
        "cancel_at_period_end": False,
        "manual_renewal": data.manual,
        "expires_at": None,
        "created_at": now,
        "updated_at": now,
    }

    if data.manual:
        from datetime import datetime, timedelta, timezone
        if plan_info["interval"] == "year":
            end = datetime.now(timezone.utc) + timedelta(days=365)
        else:
            end = datetime.now(timezone.utc) + timedelta(days=30)
        sub_doc["expires_at"] = end.isoformat()
        sub_doc["current_period_end"] = end.isoformat()
    elif settings.STRIPE_SECRET_KEY:
        price_attr = PRICE_MAP.get(data.plan)
        price_id = getattr(settings, price_attr, None) if price_attr else None
        if price_id:
            sub_doc["stripe_price_id"] = price_id

    await db["subscriptions"].insert_one(sub_doc)
    await db["cards"].update_one(
        {"_id": data.card_id},
        {"$set": {"subscription_id": sub_id, "subscription_status": "active"}},
    )

    from app.sync import sync_subscription
    await sync_subscription(sub_doc, user, "subscription_created")

    return {"subscription_id": sub_id, "status": "active"}


@router.post("/{sub_id}/cancel")
async def cancel_subscription(sub_id: str, user: dict = Depends(get_api_user)):
    sub = await db["subscriptions"].find_one({"_id": sub_id})
    if not sub or sub["user_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    now = now_iso()
    await db["subscriptions"].update_one(
        {"_id": sub_id},
        {"$set": {"status": "cancelled", "cancel_at_period_end": True, "updated_at": now}},
    )
    await db["cards"].update_one(
        {"_id": sub["card_id"]},
        {"$set": {"subscription_status": "expired"}},
    )

    from app.sync import sync_subscription
    updated_sub = {**sub, "status": "cancelled", "cancel_at_period_end": True}
    await sync_subscription(updated_sub, user, "subscription_expired")

    return {"status": "cancelled"}


@router.post("/{sub_id}/renew")
async def renew_subscription(sub_id: str, user: dict = Depends(get_api_user)):
    sub = await db["subscriptions"].find_one({"_id": sub_id})
    if not sub or sub["user_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    from datetime import datetime, timedelta, timezone
    now = now_iso()
    end = datetime.now(timezone.utc) + timedelta(days=365 if sub["interval"] == "year" else 30)

    await db["subscriptions"].update_one(
        {"_id": sub_id},
        {"$set": {"status": "active", "expires_at": end.isoformat(), "current_period_end": end.isoformat(), "updated_at": now}},
    )
    await db["cards"].update_one(
        {"_id": sub["card_id"]},
        {"$set": {"subscription_status": "active"}},
    )

    from app.sync import sync_subscription
    renewed_sub = {**sub, "status": "active", "expires_at": end.isoformat(), "current_period_end": end.isoformat()}
    await sync_subscription(renewed_sub, user, "subscription_renewed")

    return {"status": "renewed", "expires_at": end.isoformat()}
