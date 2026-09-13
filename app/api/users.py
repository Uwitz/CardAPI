import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import EmailStr

from app.auth import get_api_user, hash_password, verify_password
from app.database import db, now_iso
from app.models import UserRegister, UserLogin, UserUpdate, CardCreate
from app.idgen import gen_user_id, gen_token, gen_referral, gen_card_id

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/register")
async def register(data: UserRegister):
    if await db["users"].find_one({"email": data.email}):
        raise HTTPException(status_code=409, detail={"error": "email_exists"})
    if await db["users"].find_one({"username": data.username}):
        raise HTTPException(status_code=409, detail={"error": "username_exists"})

    user_id = gen_user_id()
    token = gen_token()
    now = now_iso()

    user = {
        "_id": user_id,
        "username": data.username,
        "email": data.email,
        "password_hash": hash_password(data.password),
        "display_name": data.display_name,
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
    await send_email(data.email, "Welcome to Uwitz Cards", "welcome", {
        "display_name": data.display_name,
        "site_url": "https://portal.uwitz.cards",
    })

    return {"id": user_id, "token": token, "display_name": data.display_name}


@router.post("/login")
async def login(data: UserLogin):
    user = await db["users"].find_one({"email": data.email})
    if not user or not verify_password(data.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail={"error": "invalid_credentials"})
    if user.get("status") == "suspended":
        raise HTTPException(status_code=403, detail={"error": "suspended"})
    return {"id": user["_id"], "token": user["token"], "display_name": user["display_name"], "role": user.get("role", "individual")}


@router.get("/me")
async def get_me(user: dict = Depends(get_api_user)):
    return {
        "id": user["_id"],
        "username": user["username"],
        "email": user["email"],
        "display_name": user["display_name"],
        "role": user.get("role", "individual"),
        "org_id": user.get("org_id"),
        "status": user.get("status", "active"),
        "referral_code": user.get("referral_code"),
        "created_at": user.get("created_at"),
    }


@router.patch("/me")
async def update_me(data: UserUpdate, user: dict = Depends(get_api_user)):
    updates = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    if updates:
        updates["updated_at"] = now_iso()
        await db["users"].update_one({"_id": user["_id"]}, {"$set": updates})
    return {"status": "updated"}


@router.get("/me/cards")
async def my_cards(user: dict = Depends(get_api_user)):
    cards = await db["cards"].find(
        {"owner_id": user["_id"]},
        {"pin": 0},
    ).sort("created_at", -1).to_list(100)
    return cards


@router.post("/me/cards")
async def create_card(data: CardCreate, user: dict = Depends(get_api_user)):
    from app.card_templates import build_vcard_from_fields

    card_id = gen_card_id()
    now = now_iso()

    vcard_data = data.vcard_data
    if data.template_fields:
        vcard_data = build_vcard_from_fields(data.template_fields)

    card = {
        "_id": card_id,
        "card_id": card_id,
        "owner_id": user["_id"],
        "org_id": user.get("org_id"),
        "card_type": data.card_type,
        "card_tier": data.card_tier,
        "vcard_data": vcard_data,
        "redirect_url": data.redirect_url,
        "plain_text": data.plain_text,
        "template_id": data.template_id,
        "template_fields": data.template_fields,
        "status": "active",
        "views": 0,
        "image_url": None,
        "created_at": now,
        "updated_at": now,
    }
    await db["cards"].insert_one(card)

    return {"card_id": card_id, "status": "created"}
