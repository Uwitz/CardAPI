import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import NotAdmin, NotAuthenticated, get_dashboard_admin, get_dashboard_user
from app.csrf import CSRF_COOKIE_NAME, CSRF_FORM_FIELD, get_or_create_csrf_token, set_csrf_cookie, verify_csrf
from app.database import db, now_iso
from app.session import clear_session_cookie, create_session_cookie

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="templates")


async def _render(request: Request, template: str, context: dict, user: dict | None = None):
    """Helper to render templates with common context."""
    ctx = {
        "request": request,
        "user": user or {},
        "now": datetime.datetime.utcnow().isoformat(),
        "csrf_token": get_or_create_csrf_token(request),
        "CSRF_FORM_FIELD": CSRF_FORM_FIELD,
        "brand": "Uwitz Cards",
    }
    ctx.update(context)
    response = templates.TemplateResponse(template, ctx)
    # Ensure CSRF cookie is set
    if request.cookies.get(CSRF_COOKIE_NAME) != ctx["csrf_token"]:
        set_csrf_cookie(response, ctx["csrf_token"])
    return response


@router.get("/dashboard/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return await _render(request, "auth/login.html", {"error": None})


@router.post("/dashboard/login")
async def login_submit(request: Request, token: str = Form(...)):
    """Verify API token, set session cookie."""
    user = await db["users"].find_one({"token": token})
    if not user:
        return await _render(request, "auth/login.html", {"error": "Invalid token. Please try again."})
    response = RedirectResponse(url="/dashboard", status_code=302)
    create_session_cookie(response, user.get("_id"), user.get("is_admin", False))
    csrf = get_or_create_csrf_token(request)
    set_csrf_cookie(response, csrf)
    return response


@router.post("/dashboard/logout")
async def logout(request: Request):
    """Logout and clear session. CSRF-protected to prevent forced-logout attacks."""
    await verify_csrf(request)
    response = RedirectResponse(url="/dashboard/login", status_code=302)
    clear_session_cookie(response)
    return response


# --- Dashboard pages ---

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_index(request: Request):
    try:
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)

    # Stats
    total_cards = await db["user_cards"].count_documents({"owner_id": user.get("_id")})
    active_cards = await db["user_cards"].count_documents({"owner_id": user.get("_id"), "status": "active"})
    total_views_agg = []
    async for c in db["user_cards"].find({"owner_id": user.get("_id")}, {"views": 1}):
        total_views_agg.append(c.get("views", 0))
    total_views = sum(total_views_agg)
    referral_reward = user.get("referral_reward", 0.0)

    # Recent cards
    recent_cards = []
    async for c in db["user_cards"].find({"owner_id": user.get("_id")}).sort("created_at", -1).limit(5):
        recent_cards.append({
            "id": c.get("_id"),
            "type": c.get("type"),
            "tier": c.get("tier"),
            "status": c.get("status"),
            "views": c.get("views", 0),
            "created_at": c.get("created_at"),
        })

    return await _render(request, "dashboard/index.html", {
        "user": user,
        "stats": {
            "total_cards": total_cards,
            "active_cards": active_cards,
            "total_views": total_views,
            "referral_reward": referral_reward,
        },
        "recent_cards": recent_cards,
    })


@router.get("/dashboard/cards", response_class=HTMLResponse)
async def dashboard_cards(request: Request):
    try:
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)

    cards = []
    async for c in db["user_cards"].find({"owner_id": user.get("_id")}).sort("created_at", -1):
        cards.append({
            "id": c.get("_id"),
            "type": c.get("type"),
            "tier": c.get("tier"),
            "status": c.get("status"),
            "views": c.get("views", 0),
            "created_at": c.get("created_at"),
        })

    return await _render(request, "dashboard/cards.html", {"user": user, "cards": cards})


@router.get("/dashboard/orders", response_class=HTMLResponse)
async def dashboard_orders(request: Request):
    try:
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)

    orders = []
    async for o in db["orders"].find({"user_id": user.get("_id")}).sort("created_at", -1):
        orders.append({
            "id": o.get("_id"),
            "tier": o.get("tier"),
            "amount": o.get("amount"),
            "currency": o.get("currency"),
            "status": o.get("status"),
            "created_at": o.get("created_at"),
        })

    return await _render(request, "dashboard/orders.html", {"user": user, "orders": orders})


@router.get("/dashboard/order/new", response_class=HTMLResponse)
async def dashboard_order_new(request: Request):
    try:
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)

    from app.api.orders import CARD_TIERS
    tiers = [{"id": k, **v} for k, v in CARD_TIERS.items()]

    return await _render(request, "dashboard/order_new.html", {"user": user, "tiers": tiers})


@router.get("/dashboard/builder", response_class=HTMLResponse)
async def dashboard_builder(request: Request):
    try:
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)

    return await _render(request, "dashboard/builder.html", {"user": user})


@router.get("/dashboard/domains", response_class=HTMLResponse)
async def dashboard_domains(request: Request):
    """Domain verification management page."""
    try:
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)

    domains = []
    async for d in db["verified_domains"].find({"user_id": user.get("_id")}).sort("created_at", -1):
        domains.append({
            "id": d.get("_id"),
            "domain": d.get("domain"),
            "verified": d.get("verified", False),
            "verification_host": d.get("verification_host"),
            "verification_record": d.get("verification_record"),
            "verified_at": d.get("verified_at"),
            "created_at": d.get("created_at"),
        })

    # Common platforms from the allowlist
    from app.api.cards import ALLOWED_REDIRECT_DOMAINS
    common_platforms = sorted(ALLOWED_REDIRECT_DOMAINS)

    return await _render(request, "dashboard/domains.html", {
        "user": user,
        "domains": domains,
        "common_platforms": common_platforms,
    })


# --- Admin pages ---

@router.get("/admin", response_class=HTMLResponse)
async def admin_index(request: Request):
    try:
        user = await get_dashboard_admin(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)
    except NotAdmin:
        return RedirectResponse(url="/dashboard", status_code=302)

    total_users = await db["users"].count_documents({})
    total_cards = await db["user_cards"].count_documents({})
    total_orders = await db["orders"].count_documents({})
    pending_orders = await db["orders"].count_documents({"status": "pending"})
    paid_orders = await db["orders"].count_documents({"status": "paid"})

    # Revenue sum
    revenue_agg = []
    async for o in db["orders"].find({"status": {"$in": ["paid", "shipped", "delivered"]}}, {"amount": 1}):
        revenue_agg.append(o.get("amount", 0))
    total_revenue = sum(revenue_agg)

    # Pending payouts count
    pending_payouts = 0
    async for u in db["users"].find({}, {"payouts": 1}):
        for p in u.get("payouts", []):
            if p.get("status") == "pending":
                pending_payouts += 1

    total_views_agg = []
    async for c in db["user_cards"].find({}, {"views": 1}):
        total_views_agg.append(c.get("views", 0))
    total_views = sum(total_views_agg)

    return await _render(request, "admin/index.html", {
        "user": user,
        "stats": {
            "total_users": total_users,
            "total_cards": total_cards,
            "total_orders": total_orders,
            "pending_orders": pending_orders,
            "paid_orders": paid_orders,
            "total_revenue": total_revenue,
            "pending_payouts": pending_payouts,
            "total_views": total_views,
        },
    })


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    try:
        user = await get_dashboard_admin(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)
    except NotAdmin:
        return RedirectResponse(url="/dashboard", status_code=302)

    users = []
    async for u in db["users"].find({}).sort("created_at", -1).limit(100):
        users.append({
            "id": u.get("_id"),
            "display_name": u.get("display_name"),
            "email": u.get("email"),
            "username": u.get("username"),
            "plan": u.get("plan"),
            "status": u.get("status"),
            "is_admin": u.get("is_admin", False),
            "created_at": u.get("created_at"),
        })

    return await _render(request, "admin/users.html", {"user": user, "users": users})


@router.get("/admin/cards", response_class=HTMLResponse)
async def admin_cards(request: Request):
    try:
        user = await get_dashboard_admin(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)
    except NotAdmin:
        return RedirectResponse(url="/dashboard", status_code=302)

    cards = []
    async for c in db["user_cards"].find({}).sort("created_at", -1).limit(100):
        cards.append({
            "id": c.get("_id"),
            "tier": c.get("tier"),
            "type": c.get("type"),
            "status": c.get("status"),
            "owner_id": c.get("owner_id"),
            "views": c.get("views", 0),
            "created_at": c.get("created_at"),
        })

    return await _render(request, "admin/cards.html", {"user": user, "cards": cards})


@router.get("/admin/orders", response_class=HTMLResponse)
async def admin_orders(request: Request):
    try:
        user = await get_dashboard_admin(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)
    except NotAdmin:
        return RedirectResponse(url="/dashboard", status_code=302)

    orders = []
    async for o in db["orders"].find({}).sort("created_at", -1).limit(100):
        orders.append({
            "id": o.get("_id"),
            "user_id": o.get("user_id"),
            "tier": o.get("tier"),
            "amount": o.get("amount"),
            "currency": o.get("currency"),
            "status": o.get("status"),
            "card_id": o.get("card_id"),
            "created_at": o.get("created_at"),
        })

    return await _render(request, "admin/orders.html", {"user": user, "orders": orders})


@router.get("/admin/payouts", response_class=HTMLResponse)
async def admin_payouts(request: Request):
    try:
        user = await get_dashboard_admin(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)
    except NotAdmin:
        return RedirectResponse(url="/dashboard", status_code=302)

    pending = []
    history = []
    async for u in db["users"].find({}, {"_id": 1, "display_name": 1, "email": 1, "payouts": 1}):
        for p in u.get("payouts", []):
            entry = {
                "id": p.get("id"),
                "amount": p.get("amount"),
                "currency": p.get("currency"),
                "status": p.get("status"),
                "created_at": p.get("created_at"),
                "user_id": u.get("_id"),
                "user_name": u.get("display_name"),
                "user_email": u.get("email"),
            }
            if p.get("status") == "pending":
                pending.append(entry)
            else:
                history.append(entry)

    return await _render(request, "admin/payouts.html", {"user": user, "pending": pending, "history": history})


@router.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(request: Request):
    try:
        user = await get_dashboard_admin(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=302)
    except NotAdmin:
        return RedirectResponse(url="/dashboard", status_code=302)

    logs = []
    try:
        async for entry in db["webhook_events"].find({}).sort("processed_at", -1).limit(100):
            logs.append({
                "timestamp": entry.get("processed_at"),
                "event": entry.get("type"),
                "level": "info",
            })
    except Exception:
        pass

    return await _render(request, "admin/logs.html", {"user": user, "logs": logs})
