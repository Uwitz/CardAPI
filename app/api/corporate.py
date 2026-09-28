import secrets
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_dashboard_corporate_admin, hash_password
from app.database import db, now_iso
from app.idgen import gen_user_id, gen_token, gen_referral, gen_short_hex
from app.models import CorporateMemberInvite, OrgDomainVerify, OrgSettingsUpdate

router = APIRouter(prefix="/api/corporate", tags=["corporate"])


async def _require_org(user: dict) -> dict:
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail={"error": "no_org"})
    org = await db["organisations"].find_one({"_id": org_id})
    if not org:
        raise HTTPException(status_code=404, detail={"error": "org_not_found"})
    return org


def _normalise_domain(domain: str) -> str | None:
    domain = (domain or "").strip().lower()
    domain = domain.rstrip(".")
    if not domain:
        return None
    if "://" in domain:
        domain = domain.split("://", 1)[1]
    if "/" in domain:
        domain = domain.split("/", 1)[0]
    return domain


def _slugify(name: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64] or "org"


# ── Org creation / onboarding ────────────────────────────────────────────────

@router.post("/org")
async def create_org(data: OrgSettingsUpdate, user: dict = Depends(get_dashboard_corporate_admin)):
    if user.get("org_id"):
        raise HTTPException(status_code=409, detail={"error": "already_has_org"})

    name = (data.name or user.get("display_name") or "My Organisation").strip()
    slug = _slugify(data.slug or name)
    clash = await db["organisations"].find_one({"slug": slug})
    if clash:
        raise HTTPException(status_code=409, detail={"error": "slug_taken"})

    now = now_iso()
    org = {
        "_id": gen_short_hex(6),
        "name": name,
        "slug": slug,
        "owner_id": user["_id"],
        "custom_domain": None,
        "domain_token": None,
        "domain_verified": False,
        "verified_domain": None,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    await db["organisations"].insert_one(org)
    await db["users"].update_one(
        {"_id": user["_id"]},
        {"$set": {"org_id": org["_id"], "role": "corporate_admin", "updated_at": now}},
    )
    return {"org": {k: v for k, v in org.items()}}


# ── Org settings ─────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_org_settings(user: dict = Depends(get_dashboard_corporate_admin)):
    org = await _require_org(user)
    public = {k: v for k, v in org.items() if k not in ("__v",)}
    return {"org": public}


@router.put("/settings")
async def update_org_settings(data: OrgSettingsUpdate, user: dict = Depends(get_dashboard_corporate_admin)):
    org = await _require_org(user)

    updates = {}
    if data.name is not None and data.name.strip() and data.name.strip() != org.get("name"):
        updates["name"] = data.name.strip()
        if not org.get("slug"):
            updates["slug"] = _slugify(data.name.strip())

    if data.slug is not None and data.slug.strip():
        slug = _slugify(data.slug)
        clash = await db["organisations"].find_one({"slug": slug, "_id": {"$ne": org["_id"]}})
        if clash:
            raise HTTPException(status_code=409, detail={"error": "slug_taken"})
        updates["slug"] = slug

    if data.custom_domain is not None or data.remove_domain:
        current = org.get("custom_domain")
        new_domain = _normalise_domain(data.custom_domain) if (data.custom_domain is not None and not data.remove_domain) else None
        if new_domain and new_domain != current:
            clash = await db["organisations"].find_one(
                {"custom_domain": new_domain, "verified_domain": True, "_id": {"$ne": org["_id"]}}
            )
            if clash:
                raise HTTPException(status_code=409, detail={"error": "domain_taken"})
            updates["domain_token"] = secrets.token_urlsafe(24)
            updates["domain_verified"] = False
            updates["verified_domain"] = None
            updates["custom_domain"] = new_domain
        elif data.remove_domain and current:
            updates["custom_domain"] = None
            updates["domain_token"] = None
            updates["domain_verified"] = False
            updates["verified_domain"] = None

    if not updates:
        return {"org": {k: v for k, v in org.items()}}

    updates["updated_at"] = now_iso()
    await db["organisations"].update_one({"_id": org["_id"]}, {"$set": updates})
    updated = await db["organisations"].find_one({"_id": org["_id"]})
    return {"org": {k: v for k, v in updated.items()}}


# ── Domain verification ──────────────────────────────────────────────────────

@router.post("/settings/domain/verify")
async def verify_org_domain(data: OrgDomainVerify, user: dict = Depends(get_dashboard_corporate_admin)):
    org = await _require_org(user)
    domain = org.get("custom_domain")
    if not domain:
        raise HTTPException(status_code=400, detail={"error": "no_domain"})

    import dns.resolver
    expected = f"uwitz-domain-verify={org.get('domain_token', '')}"
    record_name = f"_uwitz-cards.{domain}"
    found = False
    ttl = None
    answers_str = []
    try:
        answers = dns.resolver.resolve(record_name, "TXT", lifetime=5)
        for rdata in answers:
            txt = "".join(part.decode() for part in rdata.strings) if isinstance(rdata.strings, list) else str(rdata).strip('"')
            answers_str.append(txt)
            if expected in txt:
                found = True
        ttl = answers.rrset.ttl if answers.rrset else None
    except Exception:
        found = False

    if found:
        await db["organisations"].update_one(
            {"_id": org["_id"]},
            {"$set": {"domain_verified": True, "verified_domain": domain, "updated_at": now_iso()}},
        )
        return {"verified": True, "domain": domain, "ttl": ttl}
    return {
        "verified": False,
        "domain": domain,
        "expected": expected,
        "record": f"TXT {record_name} = \"{expected}\"",
        "found": answers_str,
        "ttl": ttl,
        "error": "TXT record not found or token mismatch",
    }


# ── Members ──────────────────────────────────────────────────────────────────

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
    member_id = gen_user_id()
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
        "token": gen_token(),
        "status": "active",
        "referral_code": gen_referral(),
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

    from app.sync import ensure_stripe_customer
    await ensure_stripe_customer(member)

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


# ── Cards ────────────────────────────────────────────────────────────────────

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
    data = await request.json()
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail={"error": "no_org"})

    import string as _s, random as _r
    card_id = "".join(_r.choices(_s.ascii_letters + _s.digits, k=6))
    now = now_iso()

    card = {
        "_id": gen_short_hex(4),
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
