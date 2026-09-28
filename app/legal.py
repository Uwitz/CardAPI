"""Legal page routes — privacy policy, terms of service, DPA."""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])
templates = Jinja2Templates(directory="templates")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy(request: Request):
    return templates.TemplateResponse(request, "legal/privacy.html", {
        "brand": "Uwitz Cards",
    })


@router.get("/terms", response_class=HTMLResponse)
async def terms_of_service(request: Request):
    return templates.TemplateResponse(request, "legal/terms.html", {
        "brand": "Uwitz Cards",
    })


@router.get("/dpa", response_class=HTMLResponse)
async def data_processing_addendum(request: Request):
    return templates.TemplateResponse(request, "legal/dpa.html", {
        "brand": "Uwitz Cards",
    })
