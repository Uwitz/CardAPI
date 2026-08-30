"""Uwitz Cards — FastAPI application entry point.

Modular FastAPI app:
- /api/* — REST API (JSON)
- /dashboard/*, /admin/* — HTML dashboard (Jinja2)
- /static/* — static assets (CSS, JS)
"""
import os

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, cards, domains, orders, payouts, users, webhooks
from app.api import admin_payouts  # separate router for /api/admin/payouts
from app.config import get_settings
from app.dashboard.routes import router as dashboard_router
from app.logging_config import logger, request_id_middleware, setup_logging

load_dotenv(find_dotenv())
setup_logging(get_settings().LOG_LEVEL)

app = FastAPI(
    title="Uwitz Cards API",
    version="2.0.0",
    description="NFC business card management with Stripe-powered ordering",
)

# --- Middleware ---
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(request_id_middleware)


# --- Exception handler ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled_error", path=str(request.url), method=request.method)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error"},
    )


# --- Static files ---
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


# --- API routers ---
app.include_router(cards.router)
app.include_router(users.router)
app.include_router(domains.router)
app.include_router(orders.router)
app.include_router(payouts.router)
app.include_router(admin.router)
app.include_router(admin_payouts.router)
app.include_router(webhooks.router)


# --- Dashboard (HTML) routes ---
app.include_router(dashboard_router)


# --- Health check (public) ---
@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
