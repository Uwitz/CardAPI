"""Card template API endpoints."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.card_templates import (
    TEMPLATES,
    TEMPLATE_CATEGORIES,
    get_template,
    list_templates,
    render_template_preview,
    render_template_back,
    render_template_thumbnail,
    build_vcard_from_fields,
)

router = APIRouter(prefix="/api/card-templates", tags=["card-templates"])


@router.get("")
async def get_templates():
    """List all available card templates."""
    return {
        "templates": list_templates(),
        "categories": TEMPLATE_CATEGORIES,
    }


@router.get("/{template_id}")
async def get_template_detail(template_id: str):
    """Get a single template's full definition."""
    tpl = get_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail={"error": "template_not_found"})
    return {
        "id": tpl.id,
        "name": tpl.name,
        "description": tpl.description,
        "category": tpl.category,
        "thumbnail_color": tpl.thumbnail_color,
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "type": f.type,
                "placeholder": f.placeholder,
                "required": f.required,
                "options": f.options,
            }
            for f in tpl.fields
        ],
    }


@router.get("/{template_id}/thumbnail")
async def get_thumbnail(template_id: str):
    """Render a template thumbnail as PNG."""
    try:
        png = render_template_thumbnail(template_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"error": "template_not_found"})
    return Response(content=png, media_type="image/png")


@router.post("/{template_id}/preview")
async def get_preview(template_id: str, request: Request):
    """Render a card preview with user-provided field values."""
    data = await request.json()
    fields = data.get("fields", {})
    qr_data = data.get("qr_data", "")
    try:
        png = render_template_preview(template_id, fields, qr_data)
    except ValueError:
        raise HTTPException(status_code=404, detail={"error": "template_not_found"})
    return Response(content=png, media_type="image/png")


@router.post("/{template_id}/back")
async def get_back(template_id: str, request: Request):
    """Render the card back face as PNG."""
    data = await request.json()
    fields = data.get("fields", {})
    qr_data = data.get("qr_data", "")
    card_id = data.get("card_id", "")
    try:
        png = render_template_back(template_id, fields, qr_data, card_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"error": "template_not_found"})
    return Response(content=png, media_type="image/png")


@router.post("/{template_id}/build-vcard")
async def build_vcard(template_id: str, request: Request):
    """Build a vCard string from template fields (for social/corporate cards)."""
    tpl = get_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail={"error": "template_not_found"})
    data = await request.json()
    fields = data.get("fields", {})
    vcard = build_vcard_from_fields(fields)
    return {"vcard_data": vcard}
