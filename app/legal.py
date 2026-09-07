"""Legal page routes — privacy policy, terms of service."""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])
templates = Jinja2Templates(directory="templates")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy(request: Request):
    return templates.TemplateResponse("legal/privacy.html", {
        "request": request,
        "brand": "Uwitz Cards",
    })


@router.get("/terms", response_class=HTMLResponse)
async def terms_of_service(request: Request):
    return templates.TemplateResponse("legal/terms.html", {
        "request": request,
        "brand": "Uwitz Cards",
    })
