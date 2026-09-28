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
    material = data.material or "plastic"
    if material == "aluminium" and data.card_type == "taglink":
        raise HTTPException(status_code=400, detail={"error": "aluminium_not_available_for_taglink"})
    price_key = "aluminium" if material == "aluminium" else data.card_tier
    base_price = CARD_PRICING.get(data.card_type, {}).get(price_key, 0)
    fee, total = calculate_total(base_price)

    order_id = "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(10))
    now = now_iso()

    # If linking to existing admin-created card
    card_id = data.card_id
    card = None
    if card_id:
        card = await db["cards"].find_one({"card_id": card_id})
        if not card:
            card = await db["cards"].find_one({"_id": card_id})
        if not card:
            raise HTTPException(status_code=404, detail={"error": "card_not_found"})
        # Verify card belongs to this user or user is admin
        if card.get("owner_id") != user["_id"] and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail={"error": "not_authorized"})
        # Verify card is in pending status
        if card.get("status") != "pending":
            raise HTTPException(status_code=400, detail={"error": "card_not_pending"})
        # Use card details from the card itself
        card_type = card.get("card_type")
        card_tier = card.get("card_tier")
    else:
        card_type = data.card_type
        card_tier = data.card_tier

    order = {
        "_id": order_id,
        "user_id": user["_id"],
        "org_id": user.get("org_id"),
        "card_type": card_type,
        "card_tier": card_tier,
        "material": material,
        "card_id": card_id,
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

    from app.sync import sync_order_create, sync_order_status
    await sync_order_create(order, user)

    # Free digital cards — skip Stripe
    if total == 0 or not settings.STRIPE_SECRET_KEY:
        await db["orders"].update_one(
            {"_id": order_id},
            {"$set": {"status": "paid", "updated_at": now}},
        )
        await sync_order_status({**order, "status": "paid"}, user, previous_status="pending")
        return {"order_id": order_id, "status": "paid", "checkout_url": None}

    # Aluminium is a pre-order with a dynamic price — charge amount directly.
    if material == "aluminium":
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "myr",
                    "product_data": {"name": f"Aluminium {card_type.title()} NFC Card (pre-order)"},
                    "unit_amount": int(round(total * 100)),
                },
                "quantity": 1,
            }],
            metadata={
                "order_id": order_id,
                "user_id": user["_id"],
                "card_type": card_type,
                "card_tier": card_tier,
                "material": material,
            },
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

    # Stripe Checkout (plastic physical cards)
    price_map = {
        ("social", "physical"): settings.STRIPE_PRICE_SOCIAL_YEARLY,
        ("corporate", "physical"): settings.STRIPE_PRICE_CORPORATE_YEARLY,
        ("taglink", "physical"): settings.STRIPE_PRICE_TAGLINK_MONTHLY,
    }
    price_id = price_map.get((card_type, card_tier))

    if price_id:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={
                "order_id": order_id,
                "user_id": user["_id"],
                "card_type": card_type,
                "card_tier": card_tier,
                "material": material,
            },
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
