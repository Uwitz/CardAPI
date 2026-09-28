import secrets as _secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth import get_dashboard_admin, get_dashboard_logistics_admin, hash_password, gen_activation_token
from app.database import db, now_iso
from app.config import get_settings
from app.idgen import gen_user_id, gen_token, gen_referral, gen_short_hex, gen_card_id
from app.models import AdminCardCreate
from app.sanitize import sanitize_vcard_data, validate_redirect_url, sanitize_string
from app.audit import audit_log, AuditAction

settings = get_settings()

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users")
async def list_users(skip: int = 0, limit: int = 50, user: dict = Depends(get_dashboard_admin)):
    if limit > 100:
        limit = 100
    users = await db["users"].find({}, {"password_hash": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db["users"].count_documents({})
    return {"users": users, "total": total, "skip": skip, "limit": limit}


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
    from app.sync import ensure_stripe_customer
    await ensure_stripe_customer(new_user)
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
    
    # Prevent non-admins from escalating privileges
    if "role" in data and data["role"] in ("admin", "logistics_admin") and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail={"error": "cannot_assign_role"})
    
    updates = {}
    changed_fields = []
    for field in ("display_name", "email", "role", "status", "org_id"):
        if field in data and data[field] is not None and data[field] != target.get(field):
            updates[field] = data[field]
            changed_fields.append(field)
    
    if updates:
        updates["updated_at"] = now_iso()
        await db["users"].update_one({"_id": user_id}, {"$set": updates})
        
        await audit_log(
            AuditAction.USER_UPDATED,
            actor_id=user["_id"],
            actor_role=user.get("role", "admin"),
            target_id=user_id,
            target_type="user",
            details={"changed_fields": changed_fields},
            request=request,
            success=True,
        )
    
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
async def reset_password(user_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    target = await db["users"].find_one({"_id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    new_password = _secrets.token_urlsafe(16)
    await db["users"].update_one(
        {"_id": user_id},
        {"$set": {"password_hash": hash_password(new_password), "updated_at": now_iso()}},
    )
    from app.email import send_email
    await send_email(target["email"], "Your password has been reset", "password_reset", {
        "display_name": target.get("display_name", ""),
        "reset_code": new_password,
    })
    
    await audit_log(
        AuditAction.ADMIN_RESET_PASSWORD,
        actor_id=user["_id"],
        actor_role=user.get("role", "admin"),
        target_id=user_id,
        target_type="user",
        request=request,
        success=True,
    )
    
    return {"status": "reset", "message": "New password sent via email"}


@router.post("/users/{user_id}/invite")
async def invite_user(user_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    target = await db["users"].find_one({"_id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if target.get("password_hash"):
        return {"status": "already_has_password"}

    new_password = _secrets.token_urlsafe(16)
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
    from app.sync import ensure_stripe_customer
    await ensure_stripe_customer(target)
    
    await audit_log(
        AuditAction.ADMIN_INVITE_USER,
        actor_id=user["_id"],
        actor_role=user.get("role", "admin"),
        target_id=user_id,
        target_type="user",
        request=request,
        success=True,
    )
    
    return {"status": "invited", "message": "Invitation sent via email with temporary password"}


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

    from app.sync import sync_subscription
    target = await db["users"].find_one({"_id": user_id}, {"password_hash": 0})
    async for sub in db["subscriptions"].find({"user_id": user_id, "status": "active"}):
        if target:
            await sync_subscription({**sub, "status": "active", "current_period_end": end, "expires_at": end}, target, "subscription_renewed")
    return {"status": "renewed", "subscriptions_updated": result.modified_count}


@router.post("/users/{user_id}/exempt")
async def exempt_user(user_id: str, user: dict = Depends(get_dashboard_admin)):
    await db["subscriptions"].update_many({"user_id": user_id}, {"$set": {"status": "cancelled", "updated_at": now_iso()}})
    await db["cards"].update_many({"owner_id": user_id}, {"$set": {"subscription_status": "active"}})
    from app.sync import ensure_stripe_customer
    target = await db["users"].find_one({"_id": user_id}, {"password_hash": 0})
    if target:
        await ensure_stripe_customer(target)
    return {"status": "exempted"}


# --- Cards ---

@router.get("/cards")
async def list_all_cards(skip: int = 0, limit: int = 50, user: dict = Depends(get_dashboard_admin)):
    if limit > 100:
        limit = 100
    cards = await db["cards"].find({}, {"pin": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db["cards"].count_documents({})
    return {"cards": cards, "total": total, "skip": skip, "limit": limit}


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
    for field in ("status", "frozen", "freeze_reason", "card_type", "card_tier", "owner_id"):
        if field in data:
            updates[field] = data[field]
    
    # Sanitize sensitive fields
    if "vcard_data" in data and data["vcard_data"]:
        updates["vcard_data"] = sanitize_vcard_data(data["vcard_data"])
    if "redirect_url" in data and data["redirect_url"]:
        valid, error = validate_redirect_url(data["redirect_url"])
        if not valid:
            raise HTTPException(status_code=400, detail={"error": "invalid_redirect_url", "detail": error})
        updates["redirect_url"] = sanitize_string(data["redirect_url"], max_length=2048)
    if "plain_text" in data and data["plain_text"]:
        updates["plain_text"] = sanitize_string(data["plain_text"], max_length=500)
    
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


# --- Admin Card + User Creation ---

@router.post("/cards/create-with-user")
async def create_card_with_user(data: AdminCardCreate, request: Request, user: dict = Depends(get_dashboard_admin)):
    """Create a card (status=pending) and associated user (status=pending) in one atomic operation."""
    # Check for existing user
    if await db["users"].find_one({"email": data.user_email}):
        raise HTTPException(status_code=409, detail={"error": "email_exists"})
    if await db["users"].find_one({"username": data.user_username}):
        raise HTTPException(status_code=409, detail={"error": "username_exists"})
    
    # Validate card data based on type
    if data.card_type in ("social", "corporate") and not data.vcard_data and not data.template_fields:
        raise HTTPException(status_code=400, detail={"error": "vcard_data_or_template_required"})
    if data.card_type == "taglink" and not data.redirect_url and not data.plain_text and not data.template_fields:
        raise HTTPException(status_code=400, detail={"error": "redirect_or_template_required"})
    
    now = now_iso()
    card_id = gen_card_id()
    
    # Build vCard data from template if provided
    vcard_data = data.vcard_data
    if data.template_fields:
        from app.card_templates import build_vcard_from_fields
        vcard_data = build_vcard_from_fields(data.template_fields)
    
    # Sanitize vCard data
    if vcard_data:
        vcard_data = sanitize_vcard_data(vcard_data)
    
    # Validate and sanitize redirect URL
    redirect_url = data.redirect_url
    if redirect_url:
        valid, error = validate_redirect_url(redirect_url)
        if not valid:
            raise HTTPException(status_code=400, detail={"error": "invalid_redirect_url", "detail": error})
        redirect_url = sanitize_string(redirect_url, max_length=2048)
    
    # Sanitize plain text
    plain_text = data.plain_text
    if plain_text:
        plain_text = sanitize_string(plain_text, max_length=500)
    
    # Create user with pending status
    user_id = gen_user_id()
    temp_password = _secrets.token_urlsafe(12)
    new_user = {
        "_id": user_id,
        "username": data.user_username,
        "email": data.user_email,
        "password_hash": hash_password(temp_password),
        "display_name": data.user_display_name,
        "role": data.user_role,
        "org_id": None,
        "stripe_customer_id": None,
        "token": gen_token(),
        "status": "pending",
        "referral_code": gen_referral(),
        "created_at": now,
        "updated_at": now,
    }
    await db["users"].insert_one(new_user)
    
    # Create card with pending status
    card = {
        "_id": card_id,
        "card_id": card_id,
        "owner_id": user_id,
        "org_id": None,
        "card_type": data.card_type,
        "card_tier": data.card_tier,
        "vcard_data": vcard_data,
        "redirect_url": data.redirect_url,
        "plain_text": data.plain_text,
        "template_id": data.template_id,
        "template_fields": data.template_fields,
        "status": "pending",
        "views": 0,
        "image_url": None,
        "created_at": now,
        "updated_at": now,
    }
    await db["cards"].insert_one(card)
    
    # Generate activation token for later use
    from app.auth import create_activation_token
    activation = await create_activation_token(card_id)
    activation_url = f"{settings.SITE_URL.rstrip('/')}/activate/{card_id}?token={activation['token']}"
    
    # Notify logistics team
    from app.sync import sync_admin_card_create
    await sync_admin_card_create(card, new_user)
    
    # Audit log
    await audit_log(
        AuditAction.ADMIN_CARD_CREATE_WITH_USER,
        actor_id=user["_id"],
        actor_role=user.get("role", "admin"),
        target_id=card_id,
        target_type="card",
        details={
            "user_id": user_id,
            "user_email": data.user_email,
            "card_type": data.card_type,
            "card_tier": data.card_tier,
        },
        request=request,
        success=True,
    )
    
    return {
        "card_id": card_id,
        "user_id": user_id,
        "activation_url": activation_url,
        "status": "created",
        "message": "Temporary password sent to user via email"
    }


@router.get("/cards/pending")
async def list_pending_cards(user: dict = Depends(get_dashboard_admin)):
    """List all cards with pending status."""
    cards = await db["cards"].find({"status": "pending"}, {"pin": 0}).sort("created_at", -1).to_list(500)
    # Enrich with user info
    for card in cards:
        owner = await db["users"].find_one({"_id": card.get("owner_id")}, {"password_hash": 0})
        card["owner"] = owner
    return cards


@router.get("/cards/{card_id}/activation-url")
async def get_activation_url(card_id: str, user: dict = Depends(get_dashboard_admin)):
    """Get or generate activation URL for a pending card."""
    card = await db["cards"].find_one({"card_id": card_id})
    if not card:
        card = await db["cards"].find_one({"_id": card_id})
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    
    if card.get("status") != "pending":
        raise HTTPException(status_code=400, detail={"error": "card_not_pending"})
    
    # Check if token exists and is valid
    existing_token = card.get("activation_token")
    expires_at = card.get("activation_expires_at")
    used = card.get("activation_used", False)
    
    if existing_token and not used and expires_at:
        try:
            from datetime import datetime, timezone
            exp = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) < exp:
                activation_url = f"{settings.SITE_URL.rstrip('/')}/activate/{card_id}?token={existing_token}"
                return {"activation_url": activation_url, "token": existing_token, "expires_at": expires_at}
        except ValueError:
            pass
    
    # Generate new token
    from app.auth import create_activation_token
    activation = await create_activation_token(card_id)
    activation_url = f"{settings.SITE_URL.rstrip('/')}/activate/{card_id}?token={activation['token']}"
    return {"activation_url": activation_url, "token": activation["token"], "expires_at": activation["expires_at"].isoformat()}


# --- Orders ---

@router.get("/orders")
async def list_all_orders(skip: int = 0, limit: int = 50, user: dict = Depends(get_dashboard_admin)):
    if limit > 100:
        limit = 100
    orders = await db["orders"].find({}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db["orders"].count_documents({})
    return {"orders": orders, "total": total, "skip": skip, "limit": limit}


@router.patch("/orders/{order_id}")
async def update_order(order_id: str, request: Request, user: dict = Depends(get_dashboard_logistics_admin)):
    data = await request.json()
    updates = {k: v for k, v in data.items() if k in ("status", "notes")}
    if updates:
        order = await db["orders"].find_one({"_id": order_id})
        previous_status = order.get("status") if order else None
        updates["updated_at"] = now_iso()
        await db["orders"].update_one({"_id": order_id}, {"$set": updates})
        if order and updates.get("status"):
            owner = await db["users"].find_one({"_id": order.get("user_id")}, {"password_hash": 0})
            if owner:
                from app.sync import sync_order_status
                await sync_order_status({**order, "status": updates["status"], "card_id": order.get("card_id")}, owner, previous_status=previous_status)
    return {"status": "updated"}


# --- Subscriptions ---

@router.get("/subscriptions")
async def list_all_subscriptions(skip: int = 0, limit: int = 50, user: dict = Depends(get_dashboard_admin)):
    if limit > 100:
        limit = 100
    subs = await db["subscriptions"].find({}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db["subscriptions"].count_documents({})
    return {"subscriptions": subs, "total": total, "skip": skip, "limit": limit}


@router.patch("/subscriptions/{sub_id}")
async def update_subscription(sub_id: str, request: Request, user: dict = Depends(get_dashboard_admin)):
    data = await request.json()
    updates = {k: v for k, v in data.items() if k in ("status", "expires_at", "current_period_end")}
    if updates:
        sub = await db["subscriptions"].find_one({"_id": sub_id})
        previous_status = sub.get("status") if sub else None
        updates["updated_at"] = now_iso()
        await db["subscriptions"].update_one({"_id": sub_id}, {"$set": updates})
        if sub and updates.get("status") != previous_status:
            owner = await db["users"].find_one({"_id": sub.get("user_id")}, {"password_hash": 0})
            if owner:
                event = "subscription_renewed" if updates["status"] == "active" else "subscription_expired" if updates["status"] == "cancelled" else "subscription_created"
                from app.sync import sync_subscription
                await sync_subscription({**sub, **updates}, owner, event)
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
    new_user = {"_id": user_id, "username": username, "email": email, "password_hash": hash_password(temp_password), "display_name": username, "role": "individual", "org_id": None, "stripe_customer_id": None, "token": gen_token(), "status": "active", "referral_code": gen_referral(), "created_at": now, "updated_at": now}
    await db["users"].insert_one(new_user)
    await db["cards"].update_one({"card_id": card_id}, {"$set": {"owner_id": user_id, "updated_at": now}})
    await db["user_cards"].update_one({"_id": card_id}, {"$set": {"owner_id": user_id, "updated_at": now}})
    from app.email import send_email
    await send_email(email, "Your Uwitz Cards account is ready", "corp_invite", {"display_name": username, "org_name": "Uwitz Cards", "email": email, "temp_password": temp_password, "site_url": settings.SITE_URL})
    from app.sync import ensure_stripe_customer
    await ensure_stripe_customer(new_user)
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
async def list_email_logs(skip: int = 0, limit: int = 50, user: dict = Depends(get_dashboard_admin)):
    if limit > 100:
        limit = 100
    logs = await db["email_logs"].find({}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db["email_logs"].count_documents({})
    return {"logs": logs, "total": total, "skip": skip, "limit": limit}
