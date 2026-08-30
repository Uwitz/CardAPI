import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.auth import get_api_user
from app.database import db, now_iso

router = APIRouter(prefix="/api/cards", tags=["cards"])

VCARD_RE = re.compile(r"^BEGIN:VCARD.*END:VCARD\s*$", re.DOTALL)
URL_RE = re.compile(r"^https?://.+")


# Allowlist of trusted redirect targets. Only these domains (or subdomains) are
# permitted as redirect URLs on url-type cards. This prevents phishing via NFC
# card redirects to arbitrary malicious sites. Users can also use their own
# DNS-verified domains (see app/api/domains.py).
ALLOWED_REDIRECT_DOMAINS = frozenset({
    # Brand
    "uwitz.cards",
    "uwitz.org",
    # Code & tech
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "stackoverflow.com",
    "dev.to",
    "codepen.io",
    "replit.com",
    "vercel.com",
    "netlify.com",
    "npmjs.com",
    "pypi.org",
    # Social & professional
    "linkedin.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "facebook.com",
    "fb.com",
    "threads.net",
    "mastodon.social",
    "bsky.app",
    # Media & content
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "twitch.tv",
    "medium.com",
    "substack.com",
    "deviantart.com",
    "dribbble.com",
    "behance.net",
    "figma.com",
    # Music & audio
    "spotify.com",
    "open.spotify.com",
    "soundcloud.com",
    "bandcamp.com",
    "music.apple.com",
    "podcasts.apple.com",
    # Other
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
    "tumblr.com",
    "snapchat.com",
    "discord.com",
    "discord.gg",
    "t.me",
    "telegram.me",
    "wa.me",
    "whatsapp.com",
    "signal.org",
    "calendly.com",
    "producthunt.com",
    "hackernews.com",
    "news.ycombinator.com",
    "wikipedia.org",
    "notion.so",
    "figma.com",
    "dribbble.com",
})


def _extract_host(url: str) -> str | None:
    """Extract the hostname (without port) from a URL. Returns None on parse error."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    return parsed.netloc.lower().split(":")[0]


def _matches_allowlist(host: str) -> bool:
    """Check if a hostname is in the static allowlist (exact or subdomain match)."""
    for allowed in ALLOWED_REDIRECT_DOMAINS:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


async def _is_safe_redirect(url: str, owner_id: str | None = None) -> bool:
    """Check that a URL is safe to redirect to.

    A URL is safe if:
    1. It has http/https scheme and a valid hostname
    2. The hostname matches the static allowlist, OR
    3. The hostname has been DNS-verified for the card's owner
    """
    host = _extract_host(url)
    if not host:
        return False
    # Static allowlist
    if _matches_allowlist(host):
        return True
    # User-verified domain
    if owner_id:
        doc = await db["verified_domains"].find_one({
            "domain": host,
            "user_id": owner_id,
            "verified": True,
        })
        if doc:
            return True
    return False


@router.get("/{card_id}")
async def get_card(card_id: str):
    """PUBLIC: serve a card. If vcard type, return vCard file. If url type, redirect."""
    user_card = await db["user_cards"].find_one({"_id": card_id})
    if not user_card:
        return RedirectResponse(url="https://uwitz.cards")

    if user_card.get("status") == "pending":
        return RedirectResponse(url=f"https://portal.uwitz.cards/setup/{card_id}")

    if user_card.get("type") == "vcard":
        return Response(
            content=user_card.get("content"),
            media_type="text/vcard",
            headers={"Content-Disposition": "attachment; filename=contact.vcf"},
        )
    elif user_card.get("type") == "url":
        content = user_card.get("content", "")
        owner_id = user_card.get("owner_id")
        if not URL_RE.match(content) or not await _is_safe_redirect(content, owner_id):
            return RedirectResponse(url="https://uwitz.cards")
        return RedirectResponse(url=content)
    else:
        return RedirectResponse(url="https://uwitz.cards")


@router.post("/{card_id}/activate")
async def activate_card(card_id: str, request: Request):
    """Activate a pending card with its PIN."""
    body = await request.json()
    pin = body.get("pin", "")
    if not pin:
        raise HTTPException(status_code=400, detail={"error": "pin_required"})

    user_card = await db["user_cards"].find_one({"_id": card_id})
    if not user_card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if user_card.get("status") != "pending":
        return {"status": user_card.get("status")}
    if user_card.get("pin") != pin:
        raise HTTPException(status_code=401, detail={"error": "invalid_card_pin"})

    await db["user_cards"].update_one(
        {"_id": card_id},
        {"$set": {"status": "active", "updated_at": now_iso()}},
    )
    return {"status": "active"}


@router.patch("/{card_id}")
async def update_card(card_id: str, request: Request, user: dict = Depends(get_api_user)):
    """Update a card's content (admin only)."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})

    body = await request.json()
    card_type = body.get("type")
    content = body.get("content")

    if card_type == "vcard":
        if not content or not VCARD_RE.match(content):
            raise HTTPException(status_code=400, detail={"error": "invalid_format"})
    elif card_type == "url":
        if not content or not URL_RE.match(content):
            raise HTTPException(status_code=400, detail={"error": "invalid_url"})
    else:
        raise HTTPException(status_code=400, detail={"error": "invalid_type"})

    card = await db["user_cards"].find_one({"_id": card_id})
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    # Plan expiry check
    owner = await db["users"].find_one({"_id": card.get("owner_id")})
    if owner and owner.get("plan_expiry"):
        import datetime as _dt
        if int(owner["plan_expiry"]) < int(_dt.datetime.now(_dt.timezone.utc).timestamp()):
            raise HTTPException(status_code=403, detail={"error": "plan_expired"})

    await db["user_cards"].update_one(
        {"_id": card_id},
        {"$set": {"content": content, "type": card_type, "updated_at": now_iso()}},
    )
    return {"status": "success"}


@router.delete("/{card_id}")
async def delete_card(card_id: str, user: dict = Depends(get_api_user)):
    """Delete a card (owner or admin)."""
    card = await db["user_cards"].find_one({"_id": card_id})
    if not card and not user.get("is_admin"):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if card and card.get("owner_id") != user.get("_id") and not user.get("is_admin"):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    await db["user_cards"].delete_one({"_id": card_id})
    return {"status": "success"}
