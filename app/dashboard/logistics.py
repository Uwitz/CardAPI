"""Logistics admin dashboard routes — order management, card printing, shipping."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import NotAuthenticated, NotLogisticsAdmin
from app.csrf import CSRF_FORM_FIELD, get_or_create_csrf_token, set_csrf_cookie
from app.database import db, now_iso

router = APIRouter(tags=["logistics-dashboard"])
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


@router.get("/logistics", response_class=HTMLResponse)
async def logistics_index(request: Request):
    try:
        from app.auth import get_dashboard_logistics_admin
        user = await get_dashboard_logistics_admin(request)
    except (NotAuthenticated, NotLogisticsAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    # Stats
    pending_orders = await db["orders"].count_documents({"status": "pending"})
    printing_queue = await db["orders"].count_documents({"status": "paid"})
    shipped = await db["orders"].count_documents({"status": "shipped"})
    total_orders = await db["orders"].count_documents({})

    return _render(request, "logistics/index.html", {
        "user": user,
        "stats": {
            "pending": pending_orders,
            "printing": printing_queue,
            "shipped": shipped,
            "total": total_orders,
        },
    })


@router.get("/logistics/orders", response_class=HTMLResponse)
async def logistics_orders(request: Request):
    try:
        from app.auth import get_dashboard_logistics_admin
        user = await get_dashboard_logistics_admin(request)
    except (NotAuthenticated, NotLogisticsAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    orders = await db["orders"].find({}).sort("created_at", -1).to_list(500)

    # Enrich with card and user info
    enriched = []
    for order in orders:
        card = await db["cards"].find_one({"_id": order.get("card_id")}) if order.get("card_id") else None
        usr = await db["users"].find_one({"_id": order.get("user_id")}, {"password_hash": 0}) if order.get("user_id") else None
        image = await db["card_images"].find_one({"card_id": card["_id"]}) if card else None
        enriched.append({
            **order,
            "card": card,
            "user_info": usr,
            "has_image": bool(image),
            "image": image,
        })

    return _render(request, "logistics/orders.html", {
        "user": user,
        "orders": enriched,
    })


@router.get("/logistics/orders/{order_id}", response_class=HTMLResponse)
async def logistics_order_detail(order_id: str, request: Request):
    try:
        from app.auth import get_dashboard_logistics_admin
        user = await get_dashboard_logistics_admin(request)
    except (NotAuthenticated, NotLogisticsAdmin):
        return RedirectResponse(url="/dashboard/login", status_code=303)

    order = await db["orders"].find_one({"_id": order_id})
    if not order:
        return RedirectResponse(url="/logistics/orders", status_code=303)

    card = await db["cards"].find_one({"_id": order.get("card_id")}) if order.get("card_id") else None
    usr = await db["users"].find_one({"_id": order.get("user_id")}, {"password_hash": 0}) if order.get("user_id") else None
    image = await db["card_images"].find_one({"card_id": card["_id"]}) if card else None

    return _render(request, "logistics/order_detail.html", {
        "user": user,
        "order": order,
        "card": card,
        "order_user": usr,
        "image": image,
    })
