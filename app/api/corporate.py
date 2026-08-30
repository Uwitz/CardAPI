import secrets
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_dashboard_corporate_admin, hash_password
from app.database import db, now_iso
from app.models import CorporateMemberInvite

router = APIRouter(prefix="/api/corporate", tags=["corporate"])


@router.get("/members")
async def list_members(user: dict = Depends(get_dashboard_corporate_admin)):
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail={"error": "no_org"})
    members = await db["users"].find(
        {"org_id": org_id}, {"password_hash": 0}
    ).to_list(200)
    return members


@router.post("/members")
async def invite_member(data: CorporateMemberInvite, user: dict = Depends(get_dashboard_corporate_admin)):
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail={"error": "no_org"})

    if await db["users"].find_one({"email": data.email}):
        raise HTTPException(status_code=409, detail={"error": "email_exists"})

    now = now_iso()
    member_id = secrets.token_hex(5)
    temp_password = secrets.token_urlsafe(12)

    member = {
        "_id": member_id,
        "username": data.email.split("@")[0].lower(),
        "email": data.email,
        "password_hash": hash_password(temp_password),
        "display_name": data.display_name,
        "role": "individual",
        "org_id": org_id,
        "stripe_customer_id": None,
        "token": secrets.token_hex(20),
        "status": "active",
        "referral_code": secrets.token_hex(3).upper(),
        "created_at": now,
        "updated_at": now,
    }
    await db["users"].insert_one(member)

    from app.email import send_email
    org = await db["organisations"].find_one({"_id": org_id})
    await send_email(data.email, f"You've been invited to {org.get('name', 'an organisation')}", "corp_invite", {
        "display_name": data.display_name,
        "org_name": org.get("name", "Organisation"),
        "email": data.email,
        "temp_password": temp_password,
        "site_url": "https://portal.uwitz.cards",
    })

    return {"id": member_id, "temp_password": temp_password}


@router.delete("/members/{member_id}")
async def remove_member(member_id: str, user: dict = Depends(get_dashboard_corporate_admin)):
    org_id = user.get("org_id")
    member = await db["users"].find_one({"_id": member_id, "org_id": org_id})
    if not member:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    await db["users"].update_one(
        {"_id": member_id},
        {"$set": {"org_id": None, "updated_at": now_iso()}},
    )
    return {"status": "removed"}


@router.get("/cards")
async def list_org_cards(user: dict = Depends(get_dashboard_corporate_admin)):
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail={"error": "no_org"})
    cards = await db["cards"].find(
        {"org_id": org_id}, {"pin": 0}
    ).sort("created_at", -1).to_list(200)
    return cards


@router.post("/cards")
async def create_org_card(request: Request, user: dict = Depends(get_dashboard_corporate_admin)):
    from fastapi import Request as Req
    data = await request.json()
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail={"error": "no_org"})

    import string, random as rnd
    ALPHABET = string.ascii_letters + string.digits
    card_id = "".join(rnd.choices(ALPHABET, k=6))
    now = now_iso()

    card = {
        "_id": secrets.token_hex(4),
        "card_id": card_id,
        "owner_id": user["_id"],
        "org_id": org_id,
        "card_type": data.get("card_type", "corporate"),
        "card_tier": data.get("card_tier", "digital"),
        "vcard_data": data.get("vcard_data"),
        "redirect_url": None,
        "plain_text": None,
        "nfc_chip_id": None,
        "serial_number": None,
        "image_url": None,
        "subscription_id": None,
        "subscription_status": "none",
        "frozen": False,
        "freeze_reason": None,
        "views": 0,
        "status": "active",
        "pin": None,
        "created_at": now,
        "updated_at": now,
    }
    await db["cards"].insert_one(card)
    return {"card_id": card_id, "status": "created"}
