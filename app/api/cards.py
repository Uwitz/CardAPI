import re
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.auth import get_api_user, verify_activation_token, mark_activation_used
from app.database import db, now_iso
from app.config import get_settings
from app.models import UserActivate
from app.sanitize import sanitize_vcard_data, validate_redirect_url, sanitize_string

router = APIRouter(prefix="/api/cards", tags=["cards"])
root_router = APIRouter(tags=["cards-serve"])

VCARD_RE = re.compile(r"^BEGIN:VCARD.*END:VCARD\s*$", re.DOTALL)


async def _serve_card(card_id: str):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card:
        card = await db["cards"].find_one({"_id": card_id})
    if not card:
        legacy = await db["user_cards"].find_one({"_id": card_id})
        if legacy:
            # Legacy cards (pre-v3) are grandfathered — serve directly
            card_type = legacy.get("type", "vcard")
            content = legacy.get("content", "")
            if card_type == "vcard":
                return Response(
                    content=content,
                    media_type="text/vcard",
                    headers={"Content-Disposition": f'attachment; filename="{card_id}.vcf"'},
                )
            elif content.startswith("http://") or content.startswith("https://"):
                return RedirectResponse(content)
            else:
                return Response(content=content, media_type="text/plain")
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    if card.get("status") == "pending":
        return RedirectResponse(url=f"/activate/{card_id}")

    # Subscription check — grandfathered cards (created before subscriptions, no sub_id) pass through
    sub_id = card.get("subscription_id")
    sub_status = card.get("subscription_status", "none")
    created_before_subscriptions = card.get("created_at", "") < "2026-08-30"

    if sub_id and sub_status != "active":
        await db["cards"].update_one(
            {"_id": card["_id"]},
            {"$set": {"status": "frozen", "frozen": True, "freeze_reason": "subscription_expired", "updated_at": now_iso()}},
        )
        raise HTTPException(status_code=402, detail={"error": "subscription_expired", "card_id": card.get("card_id"), "redirect": f"/subscribe/{card.get('card_id', card_id)}"})

    if not sub_id and not created_before_subscriptions:
        if card.get("status") != "frozen":
            await db["cards"].update_one(
                {"_id": card["_id"]},
                {"$set": {"status": "frozen", "frozen": True, "freeze_reason": "subscription_required", "updated_at": now_iso()}},
            )
        raise HTTPException(status_code=402, detail={"error": "subscription_required", "card_id": card.get("card_id"), "redirect": f"/subscribe/{card.get('card_id', card_id)}"})

    if card.get("status") == "frozen" and card.get("freeze_reason") in ("subscription_expired", "subscription_required"):
        raise HTTPException(status_code=402, detail={"error": card.get("freeze_reason"), "card_id": card.get("card_id"), "redirect": f"/subscribe/{card.get('card_id', card_id)}"})

    await db["cards"].update_one({"_id": card["_id"]}, {"$inc": {"views": 1}})

    card_type = card.get("card_type")
    if card_type in ("social", "corporate"):
        vcard = card.get("vcard_data", "")
        return Response(
            content=vcard,
            media_type="text/vcard",
            headers={"Content-Disposition": f'attachment; filename="{card_id}.vcf"'},
        )
    if card_type == "taglink":
        url = card.get("redirect_url")
        text = card.get("plain_text")
        if url and (url.startswith("http://") or url.startswith("https://")):
            return RedirectResponse(url)
        if text:
            return Response(content=text, media_type="text/plain")
        return Response(content="No content configured", media_type="text/plain")

    raise HTTPException(status_code=500, detail={"error": "unknown_card_type"})


@root_router.get("/{card_id}")
async def serve_card_root(card_id: str):
    return await _serve_card(card_id)


@router.get("/{card_id}")
async def get_card(card_id: str):
    return await _serve_card(card_id)


@router.patch("/{card_id}")
async def update_card(card_id: str, request: Request, user: dict = Depends(get_api_user)):
    data = await request.json()
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    updates = {}
    
    if "vcard_data" in data and data["vcard_data"]:
        updates["vcard_data"] = sanitize_vcard_data(data["vcard_data"])
    if "redirect_url" in data and data["redirect_url"]:
        valid, error = validate_redirect_url(data["redirect_url"])
        if not valid:
            raise HTTPException(status_code=400, detail={"error": "invalid_redirect_url", "detail": error})
        updates["redirect_url"] = sanitize_string(data["redirect_url"], max_length=2048)
    if "plain_text" in data and data["plain_text"]:
        updates["plain_text"] = sanitize_string(data["plain_text"], max_length=500)
    
    if updates:
        updates["updated_at"] = now_iso()
        await db["cards"].update_one({"_id": card["_id"]}, {"$set": updates})
    return {"status": "updated"}


@router.delete("/{card_id}")
async def delete_card(card_id: str, user: dict = Depends(get_api_user)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    await db["cards"].delete_one({"_id": card["_id"]})
    await db["taglink_api_keys"].delete_many({"card_id": card["_id"]})
    return {"status": "deleted"}


@router.get("/{card_id}/back", response_class=Response)
async def get_card_back(card_id: str, user: dict = Depends(get_api_user)):
    """Render the card back face from stored template data."""
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    template_id = card.get("template_id")
    if not template_id:
        raise HTTPException(status_code=404, detail={"error": "no_template"})
    from app.card_templates import render_template_back
    fields = card.get("template_fields") or {}
    qr_data = card.get("vcard_data") or fields.get("url") or ""
    png = render_template_back(template_id, fields, qr_data, card_id)
    return Response(content=png, media_type="image/png")


@router.post("/{card_id}/freeze")
async def freeze_card(card_id: str, request: Request, user: dict = Depends(get_api_user)):
    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    await db["cards"].update_one(
        {"_id": card["_id"]},
        {"$set": {"status": "frozen", "frozen": True, "freeze_reason": data.get("reason", ""), "updated_at": now_iso()}},
    )
    return {"status": "frozen"}


@router.post("/{card_id}/unfreeze")
async def unfreeze_card(card_id: str, user: dict = Depends(get_api_user)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    await db["cards"].update_one(
        {"_id": card["_id"]},
        {"$set": {"status": "active", "frozen": False, "freeze_reason": None, "updated_at": now_iso()}},
    )
    return {"status": "unfrozen"}


@router.post("/{card_id}/activate")
async def activate_card(card_id: str, request: Request):
    """Activate card with activation token (secure flow only - legacy PIN removed)."""
    data = await request.json()
    
    # Only support token-based activation (secure flow)
    token = data.get("token") or request.query_params.get("token")
    password = data.get("password")
    
    if not token:
        raise HTTPException(status_code=400, detail={"error": "activation_token_required", "message": "Activation token is required. Use the link sent via email."})
    
    # New activation flow with token
    valid, card = await verify_activation_token(card_id, token)
    if not valid or not card:
        raise HTTPException(status_code=400, detail={"error": "invalid_or_expired_token"})
    
    if card.get("status") != "pending":
        raise HTTPException(status_code=400, detail={"error": "card_not_pending"})
    
    # If password provided, this is user activation (not just card activation)
    if password:
        # Get the user and activate them
        owner_id = card.get("owner_id")
        if not owner_id:
            raise HTTPException(status_code=400, detail={"error": "no_owner"})
        
        user = await db["users"].find_one({"_id": owner_id})
        if not user:
            raise HTTPException(status_code=404, detail={"error": "user_not_found"})
        
        if user.get("status") != "pending":
            raise HTTPException(status_code=400, detail={"error": "user_not_pending"})
        
        # Hash new password and activate user
        from app.auth import hash_password
        await db["users"].update_one(
            {"_id": owner_id},
            {"$set": {"password_hash": hash_password(password), "status": "active", "updated_at": now_iso()}}
        )
        
        # Mark activation token as used
        await mark_activation_used(card_id)
        
        # Activate card
        await db["cards"].update_one(
            {"_id": card["_id"]},
            {"$set": {"status": "active", "pin": None, "updated_at": now_iso()}}
        )
        
        # Send activation complete email
        from app.sync import sync_user_activation
        await sync_user_activation(user, card)
        
        return {"status": "activated", "user_activated": True}
    
    # Just card activation (admin use - no user account to activate)
    await db["cards"].update_one(
        {"_id": card["_id"]},
        {"$set": {"status": "active", "pin": None, "updated_at": now_iso()}}
    )
    return {"status": "activated"}


@router.get("/activate/{card_id}")
async def get_activation_page(card_id: str, token: str = None):
    """Serve activation page for pending cards - redirects to frontend activation page."""
    card = await db["cards"].find_one({"card_id": card_id})
    if not card:
        card = await db["cards"].find_one({"_id": card_id})
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    
    if card.get("status") == "pending":
        # Redirect to frontend activation page with token
        activation_url = f"/activate/{card_id}"
        if token:
            activation_url += f"?token={token}"
        return RedirectResponse(url=activation_url)
    
    # If card is active, serve normally
    return await _serve_card(card_id)


@router.post("/{card_id}/convert")
async def convert_card(card_id: str, request: Request, user: dict = Depends(get_api_user)):
    from app.models import CardConvert
    from app.pricing import CONVERSION_PLAN_MAP

    data = await request.json()
    try:
        convert_data = CardConvert(**data)
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": "invalid_payload", "detail": str(e)})

    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    current_type = card.get("card_type")

    if current_type == "corporate":
        raise HTTPException(status_code=400, detail={"error": "corporate_cards_cannot_be_converted"})

    if current_type == "taglink" and convert_data.target_type == "social":
        raise HTTPException(status_code=400, detail={"error": "taglink_cannot_upgrade_to_social"})

    if current_type == convert_data.target_type:
        raise HTTPException(status_code=400, detail={"error": "already_this_type"})

    updates = {
        "card_type": convert_data.target_type,
        "card_tier": convert_data.target_tier,
        "updated_at": now_iso(),
    }

    # Transfer subscription to new plan
    conversion_key = (current_type, convert_data.target_type)
    new_plan = CONVERSION_PLAN_MAP.get(conversion_key)
    sub = await db["subscriptions"].find_one({"card_id": card["_id"], "status": "active"})
    if sub and new_plan:
        await db["subscriptions"].update_one(
            {"_id": sub["_id"]},
            {"$set": {"plan": new_plan, "updated_at": now_iso()}},
        )
        updates["subscription_plan"] = new_plan

    await db["cards"].update_one({"_id": card["_id"]}, {"$set": updates})

    return {"status": "converted", "from": current_type, "to": convert_data.target_type}
