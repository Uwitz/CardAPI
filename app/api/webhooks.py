import stripe
from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.database import db, now_iso
from app.logging_config import logger

router = APIRouter(tags=["webhooks"])
settings = get_settings()

if settings.STRIPE_SECRET_KEY:
    stripe.api_key = settings.STRIPE_SECRET_KEY


@router.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail={"error": "webhook_not_configured"})

    try:
        event = stripe.Webhook.construct_event(body, sig, settings.STRIPE_WEBHOOK_SECRET)
    except (stripe.error.SignatureVerificationError, ValueError):
        raise HTTPException(status_code=400, detail={"error": "invalid_signature"})

    event_id = event["id"]
    event_type = event["type"]

    # Idempotency check
    existing = await db["stripe_events"].find_one({"_id": event_id})
    if existing:
        return {"status": "already_processed"}

    now = now_iso()
    await db["stripe_events"].insert_one({"_id": event_id, "type": event_type, "processed_at": now})

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(event["data"]["object"])
    elif event_type == "checkout.session.expired":
        await _handle_checkout_expired(event["data"]["object"])
    elif event_type == "charge.refunded":
        await _handle_charge_refunded(event["data"]["object"])
    elif event_type == "customer.subscription.updated":
        await _handle_sub_updated(event["data"]["object"])
    elif event_type == "customer.subscription.deleted":
        await _handle_sub_deleted(event["data"]["object"])

    return {"status": "ok"}


async def _handle_checkout_completed(session):
    order_id = session.get("metadata", {}).get("order_id")
    if not order_id:
        return

    now = now_iso()
    order = await db["orders"].find_one({"_id": order_id})
    if not order:
        return

    await db["orders"].update_one(
        {"_id": order_id},
        {"$set": {"status": "paid", "stripe_session_id": session.get("id"), "updated_at": now}},
    )

    # Create card
    import secrets
    import string
    ALPHABET = string.ascii_letters + string.digits
    card_id = "".join(secrets.choice(ALPHABET) for _ in range(6))

    card_type = order.get("card_type", "social")
    card_tier = order.get("card_tier", "physical")

    card = {
        "_id": secrets.token_hex(4),
        "card_id": card_id,
        "owner_id": order["user_id"],
        "org_id": order.get("org_id"),
        "card_type": card_type,
        "card_tier": card_tier,
        "vcard_data": None,
        "redirect_url": None,
        "plain_text": None,
        "nfc_chip_id": None,
        "serial_number": None,
        "image_url": None,
        "subscription_id": None,
        "subscription_status": "none",
        "frozen": False,
        "freeze_reason": None,
        "views": 0,
        "status": "active",
        "pin": secrets.token_hex(4),
        "created_at": now,
        "updated_at": now,
    }
    await db["cards"].insert_one(card)
    await db["orders"].update_one(
        {"_id": order_id},
        {"$set": {"card_id": card["_id"], "updated_at": now}},
    )

    from app.email import send_email
    user = await db["users"].find_one({"_id": order["user_id"]})
    if user:
        await send_email(user["email"], "Payment Received", "payment_received", {
            "display_name": user.get("display_name", ""),
            "order_id": order_id,
            "amount": order.get("amount_total", 0),
            "card_type": card_type,
        })


async def _handle_checkout_expired(session):
    order_id = session.get("metadata", {}).get("order_id")
    if order_id:
        await db["orders"].update_one(
            {"_id": order_id},
            {"$set": {"status": "expired", "updated_at": now_iso()}},
        )


async def _handle_charge_refunded(charge):
    payment_intent = charge.get("payment_intent")
    if payment_intent:
        await db["orders"].update_one(
            {"stripe_payment_intent": payment_intent},
            {"$set": {"status": "refunded", "updated_at": now_iso()}},
        )


async def _handle_sub_updated(sub):
    stripe_sub_id = sub.get("id")
    if not stripe_sub_id:
        return
    now = now_iso()
    status = "active" if sub.get("status") in ("active", "trialing") else "past_due"
    if sub.get("canceled_at"):
        status = "cancelled"

    await db["subscriptions"].update_one(
        {"stripe_subscription_id": stripe_sub_id},
        {"$set": {
            "status": status,
            "current_period_end": sub.get("current_period_end"),
            "updated_at": now,
        }},
    )

    # Update card subscription status
    sub_doc = await db["subscriptions"].find_one({"stripe_subscription_id": stripe_sub_id})
    if sub_doc:
        card_status = "active" if status == "active" else "expired"
        await db["cards"].update_one(
            {"_id": sub_doc["card_id"]},
            {"$set": {"subscription_status": card_status}},
        )

        if status == "cancelled":
            from app.email import send_email
            user = await db["users"].find_one({"_id": sub_doc["user_id"]})
            if user:
                await send_email(user["email"], "Subscription Cancelled", "subscription_expired", {
                    "display_name": user.get("display_name", ""),
                    "plan": sub_doc.get("plan", ""),
                })


async def _handle_sub_deleted(sub):
    stripe_sub_id = sub.get("id")
    if not stripe_sub_id:
        return
    now = now_iso()
    await db["subscriptions"].update_one(
        {"stripe_subscription_id": stripe_sub_id},
        {"$set": {"status": "cancelled", "updated_at": now}},
    )
    sub_doc = await db["subscriptions"].find_one({"stripe_subscription_id": stripe_sub_id})
    if sub_doc:
        await db["cards"].update_one(
            {"_id": sub_doc["card_id"]},
            {"$set": {"subscription_status": "expired"}},
        )
