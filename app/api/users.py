import secrets
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import EmailStr

from app.auth import get_api_user, hash_password, verify_password
from app.database import db, now_iso
from app.models import UserRegister, UserLogin, UserUpdate, CardCreate
from app.idgen import gen_user_id, gen_token, gen_referral, gen_card_id
from app.audit import audit_log, AuditAction, get_failed_login_attempts

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

    from app.sync import ensure_stripe_customer
    await ensure_stripe_customer(user)

    return {"id": user_id, "token": token, "display_name": data.display_name}


# Account lockout settings
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_MINUTES = 15
LOCKOUT_DURATION_MINUTES = 30


@router.post("/login")
async def login(data: UserLogin, request: Request):
    # Check for account lockout
    user = await db["users"].find_one({"email": data.email})
    if user:
        since = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
        failed_attempts = await get_failed_login_attempts(user["_id"], since)
        if failed_attempts >= MAX_FAILED_ATTEMPTS:
            # Check if already locked out
            lockout_until = user.get("lockout_until")
            if lockout_until:
                try:
                    lockout_dt = datetime.fromisoformat(lockout_until.replace('Z', '+00:00'))
                    if datetime.now(timezone.utc) < lockout_dt:
                        raise HTTPException(status_code=429, detail={
                            "error": "account_locked",
                            "message": f"Too many failed attempts. Try again after {lockout_dt.isoformat()}"
                        })
                except ValueError:
                    pass
            
            # Set lockout
            lockout_until = (datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)).isoformat()
            await db["users"].update_one(
                {"_id": user["_id"]},
                {"$set": {"lockout_until": lockout_until, "updated_at": now_iso()}}
            )
            
            await audit_log(
                AuditAction.ACCOUNT_LOCKED,
                actor_id=user["_id"],
                actor_role=user.get("role", "individual"),
                target_id=user["_id"],
                target_type="user",
                details={"failed_attempts": failed_attempts, "lockout_duration_minutes": LOCKOUT_DURATION_MINUTES},
                request=request,
                success=True,
            )
            raise HTTPException(status_code=429, detail={
                "error": "account_locked",
                "message": f"Too many failed attempts. Account locked for {LOCKOUT_DURATION_MINUTES} minutes."
            })
    
    if not user or not verify_password(data.password, user.get("password_hash", "")):
        await audit_log(
            AuditAction.LOGIN_FAILED,
            actor_id=user["_id"] if user else "unknown",
            actor_role=user.get("role", "individual") if user else "unknown",
            target_id=user["_id"] if user else None,
            target_type="user",
            details={"email": data.email},
            request=request,
            success=False,
            error_message="Invalid credentials",
        )
        raise HTTPException(status_code=401, detail={"error": "invalid_credentials"})
    
    if user.get("status") == "suspended":
        await audit_log(
            AuditAction.LOGIN_FAILED,
            actor_id=user["_id"],
            actor_role=user.get("role", "individual"),
            target_id=user["_id"],
            target_type="user",
            request=request,
            success=False,
            error_message="Account suspended",
        )
        raise HTTPException(status_code=403, detail={"error": "suspended"})
    
    # Clear failed attempts on successful login
    await db["users"].update_one(
        {"_id": user["_id"]},
        {"$unset": {"lockout_until": ""}, "$set": {"updated_at": now_iso()}}
    )
    
    await audit_log(
        AuditAction.LOGIN_SUCCESS,
        actor_id=user["_id"],
        actor_role=user.get("role", "individual"),
        target_id=user["_id"],
        target_type="user",
        request=request,
        success=True,
    )
    
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
