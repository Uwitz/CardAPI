import os
import time

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.csrf import get_or_create_csrf_token
from app.database import db, ensure_indexes
from app.logging_config import logger, setup_logging
from app.security_middleware import RateLimitMiddleware, SecurityHeadersMiddleware, RequestSizeLimitMiddleware

load_dotenv(find_dotenv())
setup_logging(get_settings().LOG_LEVEL)

app = FastAPI(title="Uwitz Cards", version="3.0.0")


# --- Request ID middleware ---
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        import uuid
        rid = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


# --- Security Middleware (order matters: rate limit -> size limit -> headers -> request ID) ---
settings = get_settings()
app.add_middleware(RequestSizeLimitMiddleware, max_size=10 * 1024 * 1024)  # 10MB
app.add_middleware(RateLimitMiddleware, settings=settings)
app.add_middleware(SecurityHeadersMiddleware, settings=settings)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
)
app.add_middleware(RequestIDMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from fastapi.responses import RedirectResponse
    if hasattr(exc, 'detail') and isinstance(exc.detail, dict) and 'redirect' in exc.detail:
        return RedirectResponse(url=exc.detail['redirect'], status_code=302)
    logger.exception("unhandled_error", path=str(request.url), method=request.method)
    return JSONResponse(status_code=500, content={"error": "internal_server_error"})


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    from fastapi.responses import RedirectResponse
    if hasattr(exc, 'detail') and isinstance(exc.detail, dict) and 'redirect' in exc.detail:
        return RedirectResponse(url=exc.detail['redirect'], status_code=302)
    if exc.status_code == 404 and not request.url.path.split("/")[1] in ("api", "health") and request.url.path.startswith(("/", "/dashboard", "/admin", "/panel")):
        from fastapi.templating import Jinja2Templates
        from app.csrf import set_csrf_cookie
        templates = Jinja2Templates(directory="templates")
        token = get_or_create_csrf_token(request)
        resp = templates.TemplateResponse(request, "404.html", {
            "request": request,
            "brand": "Uwitz Cards",
            "csrf_token": token,
        }, status_code=404)
        set_csrf_cookie(resp, token)
        return resp
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})


# --- Static files ---
os.makedirs("static/uploads/cards", exist_ok=True)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


# --- Routers ---
from app.api import auth as auth_api
from app.api import cards as cards_api
from app.api import users as users_api
from app.api import orders as orders_api
from app.api import subscriptions as subs_api
from app.api import taglink as taglink_api
from app.api import images as images_api
from app.api import admin as admin_api
from app.api import corporate as corp_api
from app.api import webhooks as webhook_api
from app.api import card_templates as templates_api
from app.api import qr as qr_api
from app.legal import router as legal_router
from app.compliance_routes import router as compliance_router

app.include_router(auth_api.router)
app.include_router(cards_api.router)
app.include_router(users_api.router)
app.include_router(orders_api.router)
app.include_router(subs_api.router)
app.include_router(taglink_api.router)
app.include_router(images_api.router)
app.include_router(admin_api.router)
app.include_router(corp_api.router)
app.include_router(webhook_api.router)
app.include_router(templates_api.router)
app.include_router(qr_api.router)
app.include_router(legal_router)
app.include_router(compliance_router)

from app.dashboard.routes import router as dashboard_router
from app.dashboard.corporate import router as corp_dashboard_router
from app.dashboard.admin import router as admin_dashboard_router
from app.dashboard.logistics import router as logistics_dashboard_router

app.include_router(dashboard_router)
app.include_router(corp_dashboard_router)
app.include_router(admin_dashboard_router)
app.include_router(logistics_dashboard_router)


@app.on_event("startup")
async def startup():
    await ensure_indexes()


@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}


@app.get("/", include_in_schema=False)
async def landing_page(request: Request):
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")
    return templates.TemplateResponse(request, "landing.html", {})


@app.get("/subscribe/{card_id}", include_in_schema=False)
async def subscription_required(request: Request, card_id: str):
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")
    card = await db["cards"].find_one({"card_id": card_id})
    if not card:
        card = await db["cards"].find_one({"_id": card_id})
    return templates.TemplateResponse(request, "subscription_required.html", {
        "request": request,
        "card_id": card_id,
        "card": card,
        "brand": "Uwitz Cards",
    })


# Root card router must be LAST so it doesn't catch /health, /dashboard, etc.
app.include_router(cards_api.root_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
