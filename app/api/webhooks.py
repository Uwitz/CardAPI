import random
import string

import stripe
from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.database import db, now_iso

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_settings = get_settings()


@router.post("/stripe")
async def stripe_webhook(request: Request):
    """Stripe webhook endpoint. Verifies signature, handles events idempotently."""
    if not _settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail={"error": "webhook_not_configured"})

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, _settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail={"error": "invalid_signature"})

    event_type = event.get("type")
    event_id = event.get("id")

    # Idempotency: skip if we've already processed this event
    if await db["webhook_events"].find_one({"_id": event_id}):
        return {"status": "already_processed"}

    session = event.get("data", {}).get("object", {})
    order_id = session.get("metadata", {}).get("order_id")
    user_id = session.get("metadata", {}).get("user_id")

    if event_type == "checkout.session.completed":
        if order_id:
            order = await db["orders"].find_one({"_id": order_id})
            if order and order.get("status") != "paid":
                # Create the actual card
                card_id = "".join(random.choices(string.ascii_letters + string.digits, k=8))
                card_doc = {
                    "_id": card_id,
                    "tier": order.get("tier", "plastic"),
                    "owner_id": user_id or order.get("user_id"),
                    "type": order.get("card_type", "vcard"),
                    "content": order.get("card_content", ""),
                    "payment_id": order_id,
                    "views": 0,
                    "status": "active",
                    "version": 1.0,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
                await db["user_cards"].insert_one(card_doc)
                await db["orders"].update_one(
                    {"_id": order_id},
                    {"$set": {"status": "paid", "card_id": card_id, "updated_at": now_iso()}},
                )

    elif event_type == "checkout.session.expired":
        if order_id:
            await db["orders"].update_one(
                {"_id": order_id},
                {"$set": {"status": "expired", "updated_at": now_iso()}},
            )

    elif event_type == "charge.refunded":
        payment_intent = session.get("payment_intent")
        if payment_intent:
            order = await db["orders"].find_one({"payment_intent": payment_intent})
            if order:
                await db["orders"].update_one(
                    {"_id": order["_id"]},
                    {"$set": {"status": "refunded", "updated_at": now_iso()}},
                )

    # Record event for idempotency
    await db["webhook_events"].insert_one({
        "_id": event_id,
        "type": event_type,
        "processed_at": now_iso(),
    })

    return {"status": "ok"}
