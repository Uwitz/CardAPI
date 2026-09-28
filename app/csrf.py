import secrets
import hmac
import hashlib

from fastapi import HTTPException, Request

from app.session import read_session

CSRF_COOKIE_NAME = "uwitz_csrf"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"


def get_or_create_csrf_token(request: Request) -> str:
    """Return existing CSRF token from cookie, or create a new one bound to session."""
    session = read_session(request)
    if not session:
        # No session yet, generate token but don't bind
        token = request.cookies.get(CSRF_COOKIE_NAME)
        if not token:
            token = secrets.token_hex(32)
        return token
    
    # Session exists - bind CSRF to session
    user_id = session.get("user_id", "")
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_hex(32)
    
    # Verify token is bound to this session (HMAC)
    expected_sig = hmac.new(
        user_id.encode(), 
        token.encode(), 
        hashlib.sha256
    ).hexdigest()[:16]
    
    # If cookie has signature, verify it
    if "|" in token:
        actual_token, sig = token.split("|", 1)
        if hmac.compare_digest(sig, expected_sig):
            return actual_token
        # Invalid signature, generate new
        token = secrets.token_hex(32)
    
    # Add signature to token
    signed_token = f"{token}|{expected_sig}"
    return signed_token


def set_csrf_cookie(response, token: str, request: Request = None) -> None:
    """Set the CSRF cookie. Must NOT be httponly (JS/HTMX needs to read it)."""
    # If we have a request with session, sign the token
    signed_token = token
    if request:
        session = read_session(request)
        if session:
            user_id = session.get("user_id", "")
            if user_id:
                sig = hmac.new(
                    user_id.encode(),
                    token.encode(),
                    hashlib.sha256
                ).hexdigest()[:16]
                signed_token = f"{token}|{sig}"
    
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=signed_token,
        max_age=60 * 60 * 24 * 7,
        httponly=False,
        secure=True,
        samesite="lax",
        path="/",
    )


async def verify_csrf(request: Request) -> None:
    """Verify CSRF token on mutating methods. Raises 403 if missing or invalid."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    
    session = read_session(request)
    if not session:
        return
    
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER)
    form_token = None
    if not header_token:
        try:
            form = await request.form()
            form_token = form.get(CSRF_FORM_FIELD)
        except Exception:
            pass
    provided = header_token or form_token
    
    if not provided or not cookie_token:
        raise HTTPException(status_code=403, detail={"error": "csrf_invalid"})
    
    # Extract actual token and signature
    if "|" not in cookie_token:
        raise HTTPException(status_code=403, detail={"error": "csrf_invalid"})
    
    actual_token, sig = cookie_token.split("|", 1)
    
    # Verify signature matches session
    user_id = session.get("user_id", "")
    expected_sig = hmac.new(
        user_id.encode(),
        actual_token.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    
    if not hmac.compare_digest(sig, expected_sig):
        raise HTTPException(status_code=403, detail={"error": "csrf_invalid"})
    
    # Compare provided token with actual token
    if not hmac.compare_digest(provided, actual_token):
        raise HTTPException(status_code=403, detail={"error": "csrf_invalid"})
