import hashlib
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import db, now_iso
from app.session import create_session_cookie
from app.idgen import gen_user_id, gen_token, gen_referral

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()

ENTRA_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
ENTRA_JWKS_URL = "https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"


@router.post("/oidc/callback")
async def oidc_callback(request: Request):
    data = await request.json()
    provider = data.get("provider")
    code = data.get("code")
    redirect_uri = data.get("redirectUri")

    if not code or not provider:
        raise HTTPException(status_code=400, detail={"error": "missing_code_or_provider"})

    try:
        if provider == "entraid":
            token_data = await _exchange_entraid(code, redirect_uri)
        elif provider == "irys":
            token_data = await _exchange_irys(code, redirect_uri)
        else:
            raise HTTPException(status_code=400, detail={"error": "unknown_provider"})

        email = token_data.get("email") or token_data.get("preferred_username") or token_data.get("upn", "")
        name = token_data.get("name", email.split("@")[0] if email else "User")
        user_groups = token_data.get("groups", [])

        if not email:
            raise HTTPException(status_code=400, detail={"error": "no_email_in_token"})

        # Determine role from Entra ID groups
        if provider == "entraid":
            user_role = _determine_role_from_groups(user_groups)
        else:
            user_role = "individual"

        # Find or create user in MongoDB
        user = await db["users"].find_one({"email": email})
        now = now_iso()

        if user:
            # Update groups and role on every login
            updates = {"updated_at": now, "last_sso_provider": provider}
            if provider == "entraid":
                updates["entra_groups"] = user_groups
                updates["role"] = user_role
            await db["users"].update_one(
                {"_id": user["_id"]},
                {"$set": updates},
            )
        else:
            # Create new user from SSO
            username = email.split("@")[0].lower()
            # Ensure unique username
            base_username = username
            counter = 1
            while await db["users"].find_one({"username": username}):
                username = f"{base_username}{counter}"
                counter += 1

            user_id = gen_user_id()
            user = {
                "_id": user_id,
                "username": username,
                "email": email,
                "password_hash": "",  # SSO users don't have password
                "display_name": name,
                "role": user_role,
                "entra_groups": user_groups,
                "org_id": None,
                "stripe_customer_id": None,
                "token": gen_token(),
                "sso_provider": provider,
                "status": "active",
                "referral_code": gen_referral(),
                "created_at": now,
                "updated_at": now,
            }
            await db["users"].insert_one(user)

            from app.email import send_email
            await send_email(email, "Welcome to Uwitz Cards", "welcome", {
                "display_name": name,
                "site_url": settings.SITE_URL,
            })

            from app.sync import ensure_stripe_customer
            await ensure_stripe_customer(user)

        # Create session cookie and return JSON (frontend handles redirect)
        from fastapi.responses import JSONResponse
        resp = JSONResponse(content={"status": "ok", "session_cookie": True})
        create_session_cookie(resp, user["_id"], user.get("role") == "admin")
        return resp

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"oidc_exchange_failed: {str(e)}"})


async def _exchange_entraid(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens via Entra ID."""
    tenant = settings.ENTRA_TENANT_ID
    token_url = ENTRA_TOKEN_URL.format(tenant=tenant)

    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.ENTRA_CLIENT_ID,
            "client_secret": settings.ENTRA_CLIENT_SECRET,
            "scope": "openid profile email",
        })

    if resp.status_code != 200:
        error_detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise Exception(f"Entra token exchange failed: {error_detail}")

    token_data = resp.json()

    # Decode JWT payload to get user info
    access_token = token_data.get("access_token", "")
    if access_token:
        try:
            payload = access_token.split(".")[1]
            payload += "=" * (4 - len(payload) % 4)
            import base64
            import json
            claims = json.loads(base64.urlsafe_b64decode(payload))
            token_data["email"] = claims.get("email") or claims.get("preferred_username") or claims.get("upn", "")
            token_data["name"] = claims.get("name", "")
            # Extract groups from token if present
            if "groups" in claims:
                token_data["groups"] = claims["groups"]
        except Exception:
            pass

    # If no email from JWT, fetch from Microsoft Graph
    if not token_data.get("email") and access_token:
        async with httpx.AsyncClient() as client:
            graph_resp = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if graph_resp.status_code == 200:
                me = graph_resp.json()
                token_data["email"] = me.get("mail") or me.get("userPrincipalName", "")
                token_data["name"] = me.get("displayName", "")

    # Fetch group memberships from Graph API
    if access_token and "groups" not in token_data:
        groups = await _fetch_entra_groups(access_token)
        if groups:
            token_data["groups"] = groups

    return token_data


async def _fetch_entra_groups(access_token: str) -> list[str]:
    """Fetch user's group memberships from Microsoft Graph API."""
    groups = []
    url = "https://graph.microsoft.com/v1.0/me/memberOf?$select=displayName,id&$top=100"
    try:
        async with httpx.AsyncClient() as client:
            while url:
                resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
                if resp.status_code != 200:
                    break
                data = resp.json()
                for g in data.get("value", []):
                    groups.append(g.get("displayName", ""))
                url = data.get("@odata.nextLink")
    except Exception:
        pass
    return groups


def _determine_role_from_groups(groups: list[str]) -> str:
    """Determine user role from Entra ID group memberships."""
    group_codes = set(groups)

    # Admin groups: UE01Z, AGC001Z, AGC001S
    admin_groups = {settings.ENTRA_GROUP_ADMIN_FULL, settings.ENTRA_GROUP_ADMIN_SUPER, settings.ENTRA_GROUP_USER_ELEVATED}
    if group_codes & admin_groups:
        return "admin"

    # Logistics admin: AGC001L
    if settings.ENTRA_GROUP_LOGISTICS in group_codes:
        return "logistics_admin"

    return "individual"


async def _exchange_irys(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens via Irys."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(settings.IRYS_TOKEN_URL, json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.IRYS_CLIENT_ID,
            "client_secret": settings.IRYS_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
        })

    if resp.status_code != 200:
        raise Exception(f"Irys token exchange failed: {resp.text}")

    token_data = resp.json()
    if "error" in token_data:
        raise Exception(token_data.get("error_description", token_data["error"]))

    # Decode JWT payload
    access_token = token_data.get("access_token", "")
    if access_token:
        try:
            payload = access_token.split(".")[1]
            payload += "=" * (4 - len(payload) % 4)
            import base64
            import json
            claims = json.loads(base64.urlsafe_b64decode(payload))
            token_data["email"] = claims.get("email") or claims.get("preferred_username", "")
            token_data["name"] = claims.get("name", "")
        except Exception:
            pass

    return token_data
