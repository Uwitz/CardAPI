import re
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.auth import get_api_user
from app.database import db, now_iso

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
    for field in ("vcard_data", "redirect_url", "plain_text"):
        if field in data:
            updates[field] = data[field]
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
    data = await request.json()
    pin = data.get("pin", "")
    card = await db["cards"].find_one({"card_id": card_id})
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if card.get("status") != "pending":
        raise HTTPException(status_code=400, detail={"error": "not_pending"})
    if card.get("pin") != pin:
        raise HTTPException(status_code=400, detail={"error": "invalid_pin"})
    await db["cards"].update_one(
        {"_id": card["_id"]},
        {"$set": {"status": "active", "pin": None}},
    )
    return {"status": "activated"}
