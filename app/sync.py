import stripe

from app.config import get_settings
from app.database import db, now_iso
from app.logging_config import logger

settings = get_settings()

if settings.STRIPE_SECRET_KEY:
    stripe.api_key = settings.STRIPE_SECRET_KEY

RECEIPTS = {
    "order_created": ("Order Created", "order_created"),
    "payment_received": ("Payment Confirmed", "payment_received"),
    "subscription_created": ("Subscription Active", "subscription_created"),
    "subscription_renewed": ("Subscription Renewed", "subscription_renewed"),
    "subscription_expired": ("Subscription Cancelled", "subscription_expired"),
    "card_shipped": ("Card Shipped", "card_shipped"),
    "activation_invite": ("Activate Your Uwitz Card", "activation_invite"),
    "activation_complete": ("Card Activated Successfully", "activation_complete"),
    "logistics_notification": ("New Card Order for Processing", "logistics_notification"),
    "order_status_change": ("Order Status Update", "order_status_change"),
}


def _stripe_enabled() -> bool:
    return bool(settings.STRIPE_SECRET_KEY)


async def send_receipt(email: str, subject: str, template: str, context: dict) -> bool:
    if not email:
        return False
    from app.email import send_email
    return await send_email(email, subject, template, context)


async def _receipt(email: str, key: str, ctx: dict) -> bool:
    if key not in RECEIPTS:
        return False
    subject, template = RECEIPTS[key]
    return await send_receipt(email, subject, template, ctx)


async def ensure_stripe_customer(user: dict) -> str | None:
    if not _stripe_enabled():
        return None
    cu_id = user.get("stripe_customer_id")
    name = (user.get("display_name") or user.get("username") or "").strip()
    email = user.get("email", "")
    metadata = {
        "app": "uwitz-cards",
        "user_id": user["_id"],
        "role": user.get("role", "individual"),
        "org_id": user.get("org_id") or "",
    }
    try:
        if cu_id:
            try:
                stripe.Customer.modify(cu_id, email=email, name=name, metadata=metadata)
            except stripe.error.InvalidRequestError:
                cu_id = None
        if not cu_id:
            customer = stripe.Customer.create(email=email, name=name, metadata=metadata)
            cu_id = customer.id
            await db["users"].update_one(
                {"_id": user["_id"]},
                {"$set": {"stripe_customer_id": cu_id, "updated_at": now_iso()}},
            )
        return cu_id
    except stripe.error.StripeError as exc:
        logger.warning("stripe_customer_sync_failed", user_id=user.get("_id"), error=str(exc))
        return None


async def _attach_customer(order: dict, user: dict, cu_id: str | None) -> None:
    if cu_id and order.get("stripe_customer_id") != cu_id:
        await db["orders"].update_one(
            {"_id": order["_id"]},
            {"$set": {"stripe_customer_id": cu_id, "updated_at": now_iso()}},
        )


async def sync_order_create(order: dict, user: dict) -> str | None:
    cu_id = await ensure_stripe_customer(user)
    await _attach_customer(order, user, cu_id)
    await _receipt(user.get("email", ""), "order_created", {
        "display_name": user.get("display_name", ""),
        "order_id": order.get("_id", ""),
    })
    return cu_id


async def sync_order_status(order: dict, user: dict, previous_status: str | None = None) -> str | None:
    cu_id = await ensure_stripe_customer(user)
    await _attach_customer(order, user, cu_id)
    status = order.get("status")
    if cu_id:
        try:
            stripe.Customer.modify(cu_id, metadata={
                "last_order_id": order.get("_id", ""),
                "last_order_status": status or "",
                "paid_total": str(order.get("amount_total") or 0),
            })
        except stripe.error.StripeError as exc:
            logger.warning("stripe_order_meta_failed", order_id=order.get("_id"), error=str(exc))
    if previous_status != status:
        if status == "paid":
            await _receipt(user.get("email", ""), "payment_received", {
                "display_name": user.get("display_name", ""),
                "amount": order.get("amount_total", 0),
                "order_id": order.get("_id", ""),
                "card_type": order.get("card_type", "social"),
            })
        elif status == "shipped":
            await _receipt(user.get("email", ""), "card_shipped", {
                "display_name": user.get("display_name", ""),
                "card_id": order.get("card_id") or order.get("_id", ""),
            })
            # Also send card shipped notification with activation link if card is pending
            if order.get("card_id"):
                card = await db["cards"].find_one({"card_id": order["card_id"]})
                if card and card.get("status") == "pending":
                    await sync_card_shipped(order, card, user)
        # Send order status change notification for all status transitions
        await sync_order_status_change(order, user, previous_status or "unknown", status, order.get("tracking_number"))
    return cu_id


async def sync_subscription(sub: dict, user: dict, event: str) -> str | None:
    cu_id = await ensure_stripe_customer(user)
    if cu_id:
        try:
            stripe.Customer.modify(cu_id, metadata={
                "active_plan": sub.get("plan", "") if sub.get("status") in ("active", "trialing") else "",
                "subscription_status": sub.get("status") or "",
                "subscription_expires_at": sub.get("expires_at") or sub.get("current_period_end") or "",
            })
        except stripe.error.StripeError as exc:
            logger.warning("stripe_sub_meta_failed", sub_id=sub.get("_id"), error=str(exc))
    if event in RECEIPTS:
        await _receipt(user.get("email", ""), event, {
            "display_name": user.get("display_name", ""),
            "plan": sub.get("plan", ""),
            "expires_at": sub.get("expires_at") or sub.get("current_period_end") or "",
        })
    return cu_id


async def link_payment(order_doc_id: str, payment_intent: str | None = None,
                       charge_id: str | None = None, invoice_id: str | None = None) -> None:
    updates = {"updated_at": now_iso()}
    if payment_intent:
        updates["stripe_payment_intent"] = payment_intent
    if charge_id:
        updates["stripe_charge_id"] = charge_id
    if invoice_id:
        updates["stripe_invoice_id"] = invoice_id
    await db["orders"].update_one({"_id": order_doc_id}, {"$set": updates})


async def sync_admin_card_create(card: dict, user: dict) -> bool:
    """Notify logistics team when admin creates a card with user."""
    from app.email import send_email
    from app.config import get_settings
    settings = get_settings()
    
    # Send to logistics team (could be a dedicated email or log)
    # For now, we'll log it and could send to a logistics email
    logistics_email = "logistics@uwitz.cards"  # Configure as needed
    
    card_url = f"{settings.SITE_URL.rstrip('/')}/{card.get('card_id', '')}"
    
    return await send_email(logistics_email, "New Card Order for Processing", "logistics_notification", {
        "card_id": card.get("card_id", ""),
        "card_type": card.get("card_type", ""),
        "card_tier": card.get("card_tier", ""),
        "user_display_name": user.get("display_name", ""),
        "user_email": user.get("email", ""),
        "card_url": card_url,
        "site_url": settings.SITE_URL,
    })


async def sync_card_shipped(order: dict, card: dict, user: dict) -> bool:
    """Send activation invite to user when card is shipped."""
    from app.email import send_email
    from app.config import get_settings
    settings = get_settings()
    
    # Generate activation URL
    from app.auth import create_activation_token
    activation = await create_activation_token(card.get("card_id", ""))
    activation_url = f"{settings.SITE_URL.rstrip('/')}/activate/{card.get('card_id', '')}?token={activation['token']}"
    
    return await send_email(user.get("email", ""), "Activate Your Uwitz Card", "activation_invite", {
        "display_name": user.get("display_name", ""),
        "card_id": card.get("card_id", ""),
        "activation_url": activation_url,
        "site_url": settings.SITE_URL,
    })


async def sync_user_activation(user: dict, card: dict) -> bool:
    """Send activation confirmation email to user."""
    from app.email import send_email
    from app.config import get_settings
    settings = get_settings()
    
    return await send_email(user.get("email", ""), "Card Activated Successfully", "activation_complete", {
        "display_name": user.get("display_name", ""),
        "card_id": card.get("card_id", ""),
        "site_url": settings.SITE_URL,
    })


async def sync_order_status_change(order: dict, user: dict, previous_status: str, new_status: str, tracking_number: str = None) -> bool:
    """Send order status change notification to user."""
    from app.email import send_email
    from app.config import get_settings
    settings = get_settings()
    
    return await send_email(user.get("email", ""), "Order Status Update", "order_status_change", {
        "display_name": user.get("display_name", ""),
        "order_id": order.get("_id", ""),
        "card_type": order.get("card_type", ""),
        "previous_status": previous_status,
        "new_status": new_status,
        "tracking_number": tracking_number,
        "site_url": settings.SITE_URL,
    })