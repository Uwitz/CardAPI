from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import NotAuthenticated, NotAdmin, NotPendingUser, verify_password
from app.csrf import CSRF_FORM_FIELD, get_or_create_csrf_token, set_csrf_cookie, verify_csrf
from app.database import db, now_iso
from app.session import create_session_cookie
from app.card_templates import list_templates
from app.pricing import CARD_PRICING

router = APIRouter(tags=["admin-dashboard"])
templates = Jinja2Templates(directory="templates")


def _render(request: Request, template: str, context: dict):
    ctx = {
        "request": request,
        "user": context.pop("user", None),
        "brand": "Uwitz Cards",
        "now": now_iso(),
        "csrf_token": get_or_create_csrf_token(request),
        "CSRF_FORM_FIELD": CSRF_FORM_FIELD,
    }
    ctx.update(context)
    resp = templates.TemplateResponse(request, template, ctx)
    set_csrf_cookie(resp, get_or_create_csrf_token(request))
    return resp


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return _render(request, "admin/login.html", {"user": None})


@router.post("/admin/login")
async def admin_login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    await verify_csrf(request)

    user = await db["users"].find_one({"email": email})
    if not user or not verify_password(password, user.get("password_hash", "")):
        return _render(request, "admin/login.html", {"error": "Invalid credentials", "user": None})

    if user.get("status") == "suspended":
        return _render(request, "admin/login.html", {"error": "Account suspended", "user": None})

    if user.get("role") != "admin":
        return _render(request, "admin/login.html", {"error": "This account does not have admin access", "user": None})

    resp = RedirectResponse(url="/admin", status_code=303)
    create_session_cookie(resp, user["_id"], True)
    return resp


@router.get("/admin", response_class=HTMLResponse)
async def admin_index(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)

    stats = {
        "users": await db["users"].count_documents({}),
        "cards": await db["cards"].count_documents({}),
        "orders": await db["orders"].count_documents({}),
        "active_subscriptions": await db["subscriptions"].count_documents({"status": "active"}),
    }
    return _render(request, "admin/index.html", {"user": user, "stats": stats})


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)

    users = await db["users"].find({}, {"password_hash": 0}).sort("created_at", -1).to_list(500)
    return _render(request, "admin/users.html", {"user": user, "users": users})


@router.get("/admin/cards", response_class=HTMLResponse)
async def admin_cards(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)

    cards = await db["cards"].find({}, {"pin": 0}).sort("created_at", -1).to_list(500)
    return _render(request, "admin/cards.html", {"user": user, "cards": cards})


@router.get("/admin/orders", response_class=HTMLResponse)
async def admin_orders(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)

    orders = await db["orders"].find({}).sort("created_at", -1).to_list(500)
    return _render(request, "admin/orders.html", {"user": user, "orders": orders})


@router.get("/admin/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)

    subs = await db["subscriptions"].find({}).sort("created_at", -1).to_list(500)
    return _render(request, "admin/subscriptions.html", {"user": user, "subscriptions": subs})


@router.get("/admin/pricing", response_class=HTMLResponse)
async def admin_pricing(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)

    from app.pricing import CARD_PRICING, SUBSCRIPTION_PRICING
    return _render(request, "admin/pricing.html", {
        "user": user, "card_pricing": CARD_PRICING, "subscription_pricing": SUBSCRIPTION_PRICING,
    })


@router.get("/admin/email-logs", response_class=HTMLResponse)
async def admin_email_logs(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)

    logs = await db["email_logs"].find({}).sort("created_at", -1).to_list(200)
    return _render(request, "admin/email_logs.html", {"user": user, "logs": logs})


@router.get("/admin/orphans", response_class=HTMLResponse)
async def admin_orphans(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)

    # Find orphan cards
    user_ids = set()
    async for u in db["users"].find({}, {"_id": 1}):
        user_ids.add(u["_id"])

    orphans = []
    seen = set()
    async for card in db["cards"].find({}, {"pin": 0}):
        owner = card.get("owner_id", "")
        if not owner or owner not in user_ids:
            card["_legacy"] = False
            orphans.append(card)
            seen.add(card.get("card_id", card["_id"]))

    async for card in db["user_cards"].find({}, {"pin": 0}):
        cid = card.get("_id", "")
        if cid not in seen:
            owner = card.get("owner_id", "")
            if not owner or owner not in user_ids:
                orphans.append({
                    "_id": cid, "card_id": cid, "owner_id": owner,
                    "card_type": card.get("type", "vcard"),
                    "card_tier": card.get("tier", "plastic"),
                    "status": card.get("status", "active"),
                    "views": card.get("views", 0),
                    "created_at": card.get("created_at", ""),
                    "_legacy": True,
                })

    users = await db["users"].find({}, {"_id": 1, "display_name": 1, "email": 1}).to_list(500)
    return _render(request, "admin/orphans.html", {"user": user, "orphans": orphans, "users": users})


# --- Card Creation Wizard ---

@router.get("/admin/cards/new", response_class=HTMLResponse)
async def admin_card_new(request: Request, step: str = "card"):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    templates_list = list_templates()
    
    return _render(request, "admin/card_new.html", {
        "user": user,
        "templates": templates_list,
        "step": step,
        "card_pricing": CARD_PRICING,
    })


@router.post("/admin/cards/create", response_class=HTMLResponse)
async def admin_card_create(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    await verify_csrf(request)
    form = await request.form()
    
    # Parse form data
    card_type = form.get("card_type")
    card_tier = form.get("card_tier", "physical")
    template_id = form.get("template_id")
    vcard_data = form.get("vcard_data")
    redirect_url = form.get("redirect_url")
    plain_text = form.get("plain_text")
    
    user_email = form.get("user_email")
    user_display_name = form.get("user_display_name")
    user_username = form.get("user_username")
    user_role = form.get("user_role", "individual")
    
    # Build template_fields from form if template selected
    template_fields = None
    if template_id:
        template = next((t for t in list_templates() if t["id"] == template_id), None)
        if template:
            template_fields = {}
            for field in template["fields"]:
                value = form.get(f"field_{field['key']}")
                if value:
                    template_fields[field["key"]] = value
    
    # Call API to create card with user
    import httpx
    from app.config import get_settings
    settings = get_settings()
    
    # Use internal function instead of HTTP call
    from app.api.admin import create_card_with_user
    from app.models import AdminCardCreate
    
    card_data = AdminCardCreate(
        card_type=card_type,
        card_tier=card_tier,
        template_id=template_id,
        template_fields=template_fields,
        vcard_data=vcard_data,
        redirect_url=redirect_url,
        plain_text=plain_text,
        user_email=user_email,
        user_display_name=user_display_name,
        user_username=user_username,
        user_role=user_role,
    )
    
    # We need to call the internal logic directly
    from app.auth import hash_password, gen_activation_token
    from app.idgen import gen_user_id, gen_token, gen_referral, gen_card_id
    from app.card_templates import build_vcard_from_fields
    from app.sync import sync_admin_card_create
    import secrets as _secrets
    
    now = now_iso()
    card_id = gen_card_id()
    
    # Build vCard data
    vcard = vcard_data
    if template_fields:
        vcard = build_vcard_from_fields(template_fields)
    
    # Create user
    user_id = gen_user_id()
    temp_password = _secrets.token_urlsafe(12)
    new_user = {
        "_id": user_id,
        "username": user_username,
        "email": user_email,
        "password_hash": hash_password(temp_password),
        "display_name": user_display_name,
        "role": user_role,
        "org_id": None,
        "stripe_customer_id": None,
        "token": gen_token(),
        "status": "pending",
        "referral_code": gen_referral(),
        "created_at": now,
        "updated_at": now,
    }
    await db["users"].insert_one(new_user)
    
    # Create card
    card = {
        "_id": card_id,
        "card_id": card_id,
        "owner_id": user_id,
        "org_id": None,
        "card_type": card_type,
        "card_tier": card_tier,
        "vcard_data": vcard,
        "redirect_url": redirect_url,
        "plain_text": plain_text,
        "template_id": template_id,
        "template_fields": template_fields,
        "status": "pending",
        "views": 0,
        "image_url": None,
        "created_at": now,
        "updated_at": now,
    }
    await db["cards"].insert_one(card)
    
    # Generate activation token
    from app.auth import gen_activation_token
    from datetime import datetime, timedelta, timezone
    activation = gen_activation_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await db["cards"].update_one(
        {"card_id": card_id},
        {"$set": {
            "activation_token": activation,
            "activation_expires_at": expires_at.isoformat(),
            "activation_used": False,
        }}
    )
    
    activation_url = f"{settings.SITE_URL.rstrip('/')}/activate/{card_id}?token={activation}"
    
    # Notify logistics
    await sync_admin_card_create(card, new_user)
    
    return _render(request, "admin/card_created.html", {
        "user": user,
        "card_id": card_id,
        "activation_url": activation_url,
        "temp_password": temp_password,
        "user_email": user_email,
    })


@router.get("/admin/cards/pending", response_class=HTMLResponse)
async def admin_pending_cards(request: Request, status: str = "pending"):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    query = {"status": status} if status != "all" else {}
    cards = await db["cards"].find(query, {"pin": 0}).sort("created_at", -1).to_list(500)
    
    # Enrich with user info
    for card in cards:
        owner = await db["users"].find_one({"_id": card.get("owner_id")}, {"password_hash": 0})
        card["owner"] = owner
    
    return _render(request, "admin/cards_pending.html", {
        "user": user,
        "cards": cards,
        "current_status": status,
    })
