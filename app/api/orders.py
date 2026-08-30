import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_api_user
from app.config import get_settings
from app.database import db, now_iso

router = APIRouter(prefix="/api/orders", tags=["orders"])

_settings = get_settings()
stripe.api_key = _settings.STRIPE_SECRET_KEY

CARD_TIERS = {
    "digital": {"name": "Digital Card", "price_id": _settings.STRIPE_PRICE_DIGITAL, "amount_cents": 0},
    "plastic": {"name": "Plastic Card", "price_id": _settings.STRIPE_PRICE_PLASTIC, "amount_cents": 1500},
    "premium": {"name": "Premium Card", "price_id": _settings.STRIPE_PRICE_PREMIUM, "amount_cents": 3500},
}


@router.post("")
async def create_order(request: Request, user: dict = Depends(get_api_user)):
    """Create a card order and Stripe Checkout Session."""
    import random
    import string

    body = await request.json()
    tier = body.get("tier")
    if tier not in CARD_TIERS:
        raise HTTPException(status_code=400, detail={"error": "invalid_tier"})

    tier_info = CARD_TIERS[tier]
    order_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))

    order_doc = {
        "_id": order_id,
        "user_id": user.get("_id"),
        "tier": tier,
        "amount": tier_info["amount_cents"] / 100,
        "currency": "MYR",
        "status": "pending",
        "card_type": body.get("card_type", "vcard"),
        "card_content": body.get("card_content"),
        "notes": body.get("notes"),
        "created_at": now_iso(),
    }
    await db["orders"].insert_one(order_doc)

    checkout_url = None
    if _settings.STRIPE_SECRET_KEY and tier_info["price_id"]:
        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                line_items=[{"price": tier_info["price_id"], "quantity": 1}],
                success_url=f"{_settings.SITE_URL}/dashboard/orders?success={order_id}",
                cancel_url=f"{_settings.SITE_URL}/dashboard/orders?cancelled={order_id}",
                metadata={"order_id": order_id, "user_id": user.get("_id")},
            )
            checkout_url = session.url
            await db["orders"].update_one(
                {"_id": order_id},
                {"$set": {"payment_intent": session.payment_intent, "checkout_session_id": session.id}},
            )
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=502, detail={"error": "stripe_error", "message": str(e)})
    else:
        # No Stripe configured — mark as paid immediately (free tier)
        await db["orders"].update_one({"_id": order_id}, {"$set": {"status": "paid"}})

    return {
        "id": order_id,
        "user_id": order_doc["user_id"],
        "tier": tier,
        "amount": order_doc["amount"],
        "currency": order_doc["currency"],
        "status": order_doc["status"],
        "checkout_url": checkout_url,
        "created_at": order_doc["created_at"],
    }


@router.get("")
async def list_my_orders(page: int = 1, limit: int = 50, user: dict = Depends(get_api_user)):
    skip = (page - 1) * limit
    cursor = db["orders"].find({"user_id": user.get("_id")}).skip(skip).limit(limit)
    orders = []
    async for o in cursor:
        orders.append({
            "id": o.get("_id"),
            "tier": o.get("tier"),
            "amount": o.get("amount"),
            "currency": o.get("currency"),
            "status": o.get("status"),
            "card_id": o.get("card_id"),
            "created_at": o.get("created_at"),
        })
    total = await db["orders"].count_documents({"user_id": user.get("_id")})
    return {"orders": orders, "page": page, "limit": limit, "total": total}
