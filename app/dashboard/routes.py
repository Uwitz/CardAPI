from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import secrets
from app.auth import NotAuthenticated, hash_password, verify_password
from app.config import get_settings
from app.csrf import CSRF_FORM_FIELD, get_or_create_csrf_token, set_csrf_cookie, verify_csrf
from app.database import db, now_iso
from app.session import create_session_cookie, clear_session_cookie
from app.pricing import CARD_PRICING, SUBSCRIPTION_PRICING
from app.idgen import gen_user_id, gen_token, gen_referral

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="templates")
settings = get_settings()


def _render(request: Request, template: str, context: dict):
    ctx = {
        "request": request,
        "user": context.pop("user", None),
        "brand": "Uwitz Cards",
        "now": now_iso(),
        "csrf_token": get_or_create_csrf_token(request),
        "CSRF_FORM_FIELD": CSRF_FORM_FIELD,
        "card_pricing": CARD_PRICING,
        "subscription_pricing": SUBSCRIPTION_PRICING,
    }
    ctx.update(context)
    resp = templates.TemplateResponse(template, ctx)
    set_csrf_cookie(resp, get_or_create_csrf_token(request))
    return resp


# --- Login / Logout ---

@router.get("/dashboard/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _render(request, "auth/login.html", {"user": None})


@router.post("/dashboard/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    user = await db["users"].find_one({"email": email})
    if not user or not verify_password(password, user.get("password_hash", "")):
        return _render(request, "auth/login.html", {"error": "Invalid email or password", "user": None})

    if user.get("status") == "suspended":
        return _render(request, "auth/login.html", {"error": "Account suspended", "user": None})

    resp = RedirectResponse(url="/dashboard", status_code=303)
    create_session_cookie(resp, user["_id"], user.get("role") == "admin")
    return resp


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return _render(request, "auth/register.html", {"user": None})


@router.post("/register")
async def register_submit(
    request: Request,
    display_name: str = Form(...),
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    import re
    username = username.strip().lower()
    if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", username):
        return _render(request, "auth/register.html", {"error": "Invalid username format", "user": None})

    if await db["users"].find_one({"email": email}):
        return _render(request, "auth/register.html", {"error": "Email already registered", "user": None})
    if await db["users"].find_one({"username": username}):
        return _render(request, "auth/register.html", {"error": "Username taken", "user": None})

    from app.auth import hash_password as hp
    now = now_iso()
    user_id = gen_user_id()
    token = gen_token()

    user = {
        "_id": user_id,
        "username": username,
        "email": email,
        "password_hash": hp(password),
        "display_name": display_name,
        "role": "individual",
        "org_id": None,
        "stripe_customer_id": None,
        "token": token,
        "status": "active",
        "referral_code": gen_referral(),
        "created_at": now,
        "updated_at": now,
    }
    await db["users"].insert_one(user)

    from app.email import send_email
    await send_email(email, "Welcome to Uwitz Cards", "welcome", {
        "display_name": display_name,
        "site_url": settings.SITE_URL,
    })

    resp = RedirectResponse(url="/dashboard", status_code=303)
    create_session_cookie(resp, user_id, False)
    return resp


@router.post("/dashboard/logout")
async def logout(request: Request):
    await verify_csrf(request)
    resp = RedirectResponse(url="/dashboard/login", status_code=303)
    clear_session_cookie(resp)
    return resp


# --- Dashboard overview ---

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_index(request: Request):
    try:
        from app.auth import get_dashboard_user
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=303)

    cards = await db["cards"].find({"owner_id": user["_id"]}).sort("created_at", -1).to_list(10)
    orders = await db["orders"].find({"user_id": user["_id"]}).sort("created_at", -1).to_list(5)
    subs = await db["subscriptions"].find({"user_id": user["_id"], "status": "active"}).to_list(10)

    return _render(request, "dashboard/index.html", {
        "user": user,
        "cards": cards,
        "orders": orders,
        "subscriptions": subs,
    })


# --- Cards ---

@router.get("/dashboard/cards", response_class=HTMLResponse)
async def dashboard_cards(request: Request):
    try:
        from app.auth import get_dashboard_user
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=303)

    cards = await db["cards"].find({"owner_id": user["_id"]}).sort("created_at", -1).to_list(200)
    return _render(request, "dashboard/cards.html", {"user": user, "cards": cards})


@router.get("/dashboard/cards/new", response_class=HTMLResponse)
async def dashboard_card_new(request: Request):
    try:
        from app.auth import get_dashboard_user
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=303)

    return _render(request, "dashboard/card_new.html", {"user": user})


@router.get("/dashboard/cards/{card_id}", response_class=HTMLResponse)
async def dashboard_card_detail(card_id: str, request: Request):
    try:
        from app.auth import get_dashboard_user
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=303)

    card = await db["cards"].find_one({"card_id": card_id, "owner_id": user["_id"]})
    if not card:
        return RedirectResponse(url="/dashboard/cards", status_code=303)

    image = await db["card_images"].find_one({"card_id": card["_id"]})
    keys = []
    if card.get("card_type") == "taglink":
        keys = await db["taglink_api_keys"].find(
            {"card_id": card["_id"]}, {"key_hash": 0}
        ).to_list(50)

    return _render(request, "dashboard/card_detail.html", {
        "user": user, "card": card, "image": image, "taglink_keys": keys,
    })


# --- Orders ---

@router.get("/dashboard/orders", response_class=HTMLResponse)
async def dashboard_orders(request: Request):
    try:
        from app.auth import get_dashboard_user
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=303)

    orders = await db["orders"].find({"user_id": user["_id"]}).sort("created_at", -1).to_list(200)
    return _render(request, "dashboard/orders.html", {"user": user, "orders": orders})


@router.get("/dashboard/orders/new", response_class=HTMLResponse)
async def dashboard_order_new(request: Request):
    try:
        from app.auth import get_dashboard_user
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=303)

    return _render(request, "dashboard/order_new.html", {"user": user})


# --- Subscriptions ---

@router.get("/dashboard/subscriptions", response_class=HTMLResponse)
async def dashboard_subscriptions(request: Request):
    try:
        from app.auth import get_dashboard_user
        user = await get_dashboard_user(request)
    except NotAuthenticated:
        return RedirectResponse(url="/dashboard/login", status_code=303)

    subs = await db["subscriptions"].find({"user_id": user["_id"]}).sort("created_at", -1).to_list(200)
    return _render(request, "dashboard/subscriptions.html", {"user": user, "subscriptions": subs})
