import json

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

_settings = get_settings()
_serializer = URLSafeTimedSerializer(_settings.SESSION_SECRET, salt="uwitz-cards-session")

SESSION_COOKIE_NAME = "uwitz_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def create_session_cookie(response: Response, user_id: str, is_admin: bool) -> None:
    """Sign and set the session cookie on the response."""
    payload = json.dumps({"user_id": user_id, "is_admin": bool(is_admin)})
    signed = _serializer.dumps(payload)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=signed,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def read_session(request: Request) -> dict | None:
    """Read and verify the session cookie. Returns session payload dict or None."""
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return None
    try:
        payload = _serializer.loads(raw, max_age=SESSION_MAX_AGE)
        return json.loads(payload)
    except (BadSignature, SignatureExpired):
        return None
