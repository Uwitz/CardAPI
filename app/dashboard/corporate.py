from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import NotAuthenticated, NotCorporateAdmin
from app.csrf import CSRF_FORM_FIELD, get_or_create_csrf_token, set_csrf_cookie
from app.database import db, now_iso

router = APIRouter(tags=["corporate-dashboard"])
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


@router.get("/corporate", response_class=HTMLResponse)
async def corporate_index(request: Request):
    try:
        from app.auth import get_dashboard_corporate_admin
        user = await get_dashboard_corporate_admin(request)
    except (NotAuthenticated, NotCorporateAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    org = await db["organisations"].find_one({"_id": user.get("org_id")})
    cards = await db["cards"].find({"org_id": user.get("org_id")}).sort("created_at", -1).to_list(10)
    members = await db["users"].find({"org_id": user.get("org_id")}).to_list(100)

    return _render(request, "corporate/index.html", {
        "user": user, "org": org, "cards": cards, "members": members,
        "corp_accent": True,
    })


@router.get("/corporate/cards", response_class=HTMLResponse)
async def corporate_cards(request: Request):
    try:
        from app.auth import get_dashboard_corporate_admin
        user = await get_dashboard_corporate_admin(request)
    except (NotAuthenticated, NotCorporateAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    cards = await db["cards"].find({"org_id": user.get("org_id")}).sort("created_at", -1).to_list(200)
    return _render(request, "corporate/cards.html", {"user": user, "cards": cards, "corp_accent": True})


@router.get("/corporate/members", response_class=HTMLResponse)
async def corporate_members(request: Request):
    try:
        from app.auth import get_dashboard_corporate_admin
        user = await get_dashboard_corporate_admin(request)
    except (NotAuthenticated, NotCorporateAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    members = await db["users"].find({"org_id": user.get("org_id")}).to_list(200)
    return _render(request, "corporate/members.html", {"user": user, "members": members, "corp_accent": True})


@router.get("/corporate/orders", response_class=HTMLResponse)
async def corporate_orders(request: Request):
    try:
        from app.auth import get_dashboard_corporate_admin
        user = await get_dashboard_corporate_admin(request)
    except (NotAuthenticated, NotCorporateAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    orders = await db["orders"].find({"org_id": user.get("org_id")}).sort("created_at", -1).to_list(200)
    return _render(request, "corporate/orders.html", {"user": user, "orders": orders, "corp_accent": True})
