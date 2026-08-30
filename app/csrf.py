import secrets

from fastapi import HTTPException, Request

from app.session import read_session

CSRF_COOKIE_NAME = "uwitz_csrf"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"


def get_or_create_csrf_token(request: Request) -> str:
    """Return existing CSRF token from cookie, or create a new one."""
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_hex(32)
    return token


def set_csrf_cookie(response, token: str) -> None:
    """Set the CSRF cookie. Must NOT be httponly (JS/HTMX needs to read it)."""
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
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
    if not read_session(request):
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
    if not provided or not cookie_token or provided != cookie_token:
        raise HTTPException(status_code=403, detail={"error": "csrf_invalid"})
