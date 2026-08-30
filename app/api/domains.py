"""Domain verification via DNS TXT records.

Users prove ownership of a domain by adding a TXT record, then can use
that domain as a redirect target on URL-type cards.

DNS record format (subdomain approach — keeps root clean):
  Host:  _uwitz-verify.<user-domain>
  Type:  TXT
  Value: uwitz-cards-token=<token>

Example:
  Host:  _uwitz-verify.mycompany.com
  Type:  TXT
  Value: uwitz-cards-token=a1b2c3d4e5f6...
"""
import re
import secrets

import dns.resolver
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_api_user
from app.database import db, now_iso

router = APIRouter(prefix="/api/domains", tags=["domains"])

DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.IGNORECASE,
)

VERIFICATION_PREFIX = "_uwitz-verify"
VERIFICATION_KEY = "uwitz-cards-token"


def _generate_token() -> str:
    """Generate a high-entropy verification token (32 bytes = 64 hex chars)."""
    return secrets.token_hex(32)


def _build_verification_host(domain: str) -> str:
    """Return the DNS host where the TXT record should be added."""
    return f"{VERIFICATION_PREFIX}.{domain}"


def _build_verification_record(token: str) -> str:
    """Return the full TXT record value the user should add."""
    return f"{VERIFICATION_KEY}={token}"


async def _check_dns_txt(host: str, expected_record: str) -> bool:
    """Query DNS TXT records for `host` and check if any match `expected_record`.

    Uses the dnspython async resolver. Returns True on match, False on any error
    (NXDOMAIN, NoAnswer, timeout, etc.) — we never raise to the caller.
    """
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = 5
        resolver.lifetime = 10
        answers = await resolver.resolve(host, "TXT", raise_on_no_answer=False)
        if not answers:
            return False
        for rdata in answers:
            # rdata.strings is a list of byte strings; concatenate for long records
            try:
                txt_value = "".join(s.decode("utf-8", errors="ignore") for s in rdata.strings)
            except Exception:
                txt_value = str(rdata)
            # Normalize whitespace and compare
            normalized = " ".join(txt_value.split())
            if normalized == " ".join(expected_record.split()):
                return True
            # Also check if the token is present anywhere in any TXT record
            # (some providers split long TXT into multiple strings)
            if expected_record in normalized:
                return True
        return False
    except Exception:
        return False


@router.get("")
async def list_domains(user: dict = Depends(get_api_user)):
    """List all domains owned by the authenticated user."""
    domains = []
    async for d in db["verified_domains"].find({"user_id": user.get("_id")}).sort("created_at", -1):
        domains.append({
            "id": d.get("_id"),
            "domain": d.get("domain"),
            "verified": d.get("verified", False),
            "verification_host": d.get("verification_host"),
            "verification_record": d.get("verification_record"),
            "verified_at": d.get("verified_at"),
            "created_at": d.get("created_at"),
        })
    return {"domains": domains}


@router.post("")
async def add_domain(request: Request, user: dict = Depends(get_api_user)):
    """Add a domain for DNS verification. Generates a unique token."""
    body = await request.json()
    domain_raw = (body.get("domain") or "").strip().lower()
    # Strip protocol and path
    domain_raw = re.sub(r"^https?://", "", domain_raw)
    domain_raw = domain_raw.split("/")[0]

    if not DOMAIN_RE.match(domain_raw):
        raise HTTPException(status_code=400, detail={"error": "invalid_domain"})

    # Check if this domain is already verified by ANY user
    existing_verified = await db["verified_domains"].find_one({
        "domain": domain_raw,
        "verified": True,
    })
    if existing_verified:
        raise HTTPException(status_code=409, detail={"error": "domain_already_verified_by_another_user"})

    # Check if this user already has a pending or verified entry for this domain
    existing_user = await db["verified_domains"].find_one({
        "user_id": user.get("_id"),
        "domain": domain_raw,
    })
    if existing_user:
        return {
            "id": existing_user["_id"],
            "domain": existing_user["domain"],
            "verified": existing_user.get("verified", False),
            "verification_host": existing_user["verification_host"],
            "verification_record": existing_user["verification_record"],
            "verified_at": existing_user.get("verified_at"),
            "created_at": existing_user["created_at"],
        }

    token = _generate_token()
    host = _build_verification_host(domain_raw)
    record = _build_verification_record(token)

    doc = {
        "_id": token[:16],  # short ID for URL, not the secret token itself
        "domain": domain_raw,
        "user_id": user.get("_id"),
        "verified": False,
        "verification_token": token,
        "verification_host": host,
        "verification_record": record,
        "verified_at": None,
        "created_at": now_iso(),
    }
    await db["verified_domains"].insert_one(doc)
    return {
        "id": doc["_id"],
        "domain": doc["domain"],
        "verified": False,
        "verification_host": host,
        "verification_record": record,
        "verified_at": None,
        "created_at": doc["created_at"],
    }


@router.post("/{domain_id}/verify")
async def verify_domain(domain_id: str, user: dict = Depends(get_api_user)):
    """Check DNS TXT records and mark domain as verified if the token matches."""
    doc = await db["verified_domains"].find_one({
        "_id": domain_id,
        "user_id": user.get("_id"),
    })
    if not doc:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if doc.get("verified"):
        return {
            "id": doc["_id"],
            "domain": doc["domain"],
            "verified": True,
            "verified_at": doc.get("verified_at"),
        }

    host = doc["verification_host"]
    expected = doc["verification_record"]
    ok = await _check_dns_txt(host, expected)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={"error": "txt_record_not_found", "host": host, "expected": expected},
        )

    await db["verified_domains"].update_one(
        {"_id": domain_id},
        {"$set": {"verified": True, "verified_at": now_iso(), "updated_at": now_iso()}},
    )
    return {
        "id": doc["_id"],
        "domain": doc["domain"],
        "verified": True,
        "verified_at": now_iso(),
    }


@router.delete("/{domain_id}")
async def delete_domain(domain_id: str, user: dict = Depends(get_api_user)):
    """Remove a domain. Only the owner can delete."""
    doc = await db["verified_domains"].find_one({
        "_id": domain_id,
        "user_id": user.get("_id"),
    })
    if not doc:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    await db["verified_domains"].delete_one({"_id": domain_id})
    return {"status": "deleted"}


# --- Helper for cards.py: check if domain is verified ---
async def is_domain_verified_for_user(domain: str, user_id: str) -> bool:
    """Check if a domain has been DNS-verified by the given user."""
    domain = domain.lower()
    doc = await db["verified_domains"].find_one({
        "domain": domain,
        "user_id": user_id,
        "verified": True,
    })
    return doc is not None
