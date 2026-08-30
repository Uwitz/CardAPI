import secrets
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_api_user
from app.config import get_settings
from app.database import db, now_iso
from app.models import OrderCreate
from app.pricing import CARD_PRICING, calculate_total

router = APIRouter(prefix="/api/orders", tags=["orders"])
settings = get_settings()

if settings.STRIPE_SECRET_KEY:
    stripe.api_key = settings.STRIPE_SECRET_KEY


@router.get("")
async def list_orders(user: dict = Depends(get_api_user)):
    orders = await db["orders"].find(
        {"user_id": user["_id"]}
    ).sort("created_at", -1).to_list(100)
    return orders


@router.post("")
async def create_order(data: OrderCreate, user: dict = Depends(get_api_user)):
    base_price = CARD_PRICING.get(data.card_type, {}).get(data.card_tier, 0)
    fee, total = calculate_total(base_price)

    order_id = "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(10))
    now = now_iso()

    order = {
        "_id": order_id,
        "user_id": user["_id"],
        "org_id": user.get("org_id"),
        "card_type": data.card_type,
        "card_tier": data.card_tier,
        "card_id": None,
        "amount_subtotal": base_price,
        "amount_fees": fee,
        "amount_total": total,
        "stripe_session_id": None,
        "stripe_payment_intent": None,
        "status": "pending",
        "shipping_address": data.shipping_address,
        "notes": data.notes,
        "created_at": now,
        "updated_at": now,
    }
    await db["orders"].insert_one(order)

    # Free digital cards — skip Stripe
    if total == 0 or not settings.STRIPE_SECRET_KEY:
        await db["orders"].update_one(
            {"_id": order_id},
            {"$set": {"status": "paid", "updated_at": now}},
        )
        return {"order_id": order_id, "status": "paid", "checkout_url": None}

    # Stripe Checkout
    price_map = {
        ("social", "physical"): settings.STRIPE_PRICE_SOCIAL_YEARLY,
        ("corporate", "physical"): settings.STRIPE_PRICE_CORPORATE_YEARLY,
        ("taglink", "physical"): settings.STRIPE_PRICE_TAGLINK_MONTHLY,
    }
    price_id = price_map.get((data.card_type, data.card_tier))

    if price_id:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={"order_id": order_id, "user_id": user["_id"], "card_type": data.card_type, "card_tier": data.card_tier},
            success_url=f"{settings.SITE_URL}/dashboard/orders?paid={order_id}",
            cancel_url=f"{settings.SITE_URL}/dashboard/orders/new",
        )
        await db["orders"].update_one(
            {"_id": order_id},
            {"$set": {
                "stripe_session_id": session.id,
                "stripe_payment_intent": session.payment_intent,
                "updated_at": now,
            }},
        )
        return {"order_id": order_id, "checkout_url": session.url}

    return {"order_id": order_id, "status": "created", "checkout_url": None}
