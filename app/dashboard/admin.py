from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import NotAuthenticated, NotAdmin
from app.csrf import CSRF_FORM_FIELD, get_or_create_csrf_token, set_csrf_cookie
from app.database import db, now_iso

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
    resp = templates.TemplateResponse(template, ctx)
    set_csrf_cookie(resp, get_or_create_csrf_token(request))
    return resp


@router.get("/admin", response_class=HTMLResponse)
async def admin_index(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

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
        return RedirectResponse(url="/dashboard/login", status_code=303)

    users = await db["users"].find({}, {"password_hash": 0}).sort("created_at", -1).to_list(500)
    return _render(request, "admin/users.html", {"user": user, "users": users})


@router.get("/admin/cards", response_class=HTMLResponse)
async def admin_cards(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    cards = await db["cards"].find({}, {"pin": 0}).sort("created_at", -1).to_list(500)
    return _render(request, "admin/cards.html", {"user": user, "cards": cards})


@router.get("/admin/orders", response_class=HTMLResponse)
async def admin_orders(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    orders = await db["orders"].find({}).sort("created_at", -1).to_list(500)
    return _render(request, "admin/orders.html", {"user": user, "orders": orders})


@router.get("/admin/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    subs = await db["subscriptions"].find({}).sort("created_at", -1).to_list(500)
    return _render(request, "admin/subscriptions.html", {"user": user, "subscriptions": subs})


@router.get("/admin/pricing", response_class=HTMLResponse)
async def admin_pricing(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

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
        return RedirectResponse(url="/dashboard/login", status_code=303)

    logs = await db["email_logs"].find({}).sort("created_at", -1).to_list(200)
    return _render(request, "admin/email_logs.html", {"user": user, "logs": logs})


@router.get("/admin/orphans", response_class=HTMLResponse)
async def admin_orphans(request: Request):
    try:
        from app.auth import get_dashboard_admin
        user = await get_dashboard_admin(request)
    except (NotAuthenticated, NotAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

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
