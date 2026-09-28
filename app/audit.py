"""Audit logging for security-sensitive actions"""

import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from enum import Enum

from app.database import db
from app.config import get_settings


class AuditAction(str, Enum):
    """Types of auditable actions."""
    # Auth
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    PASSWORD_RESET = "password_reset"
    PASSWORD_CHANGED = "password_changed"
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_UNLOCKED = "account_unlocked"
    
    # User management
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DELETED = "user_deleted"
    USER_ROLE_CHANGED = "user_role_changed"
    USER_STATUS_CHANGED = "user_status_changed"
    
    # Card management
    CARD_CREATED = "card_created"
    CARD_UPDATED = "card_updated"
    CARD_DELETED = "card_deleted"
    CARD_ACTIVATED = "card_activated"
    CARD_FROZEN = "card_frozen"
    CARD_UNFROZEN = "card_unfrozen"
    CARD_STATUS_CHANGED = "card_status_changed"
    
    # Admin actions
    ADMIN_CARD_CREATE_WITH_USER = "admin_card_create_with_user"
    ADMIN_RESET_PASSWORD = "admin_reset_password"
    ADMIN_INVITE_USER = "admin_invite_user"
    ADMIN_RENEW_USER = "admin_renew_user"
    ADMIN_EXEMPT_USER = "admin_exempt_user"
    
    # Order management
    ORDER_CREATED = "order_created"
    ORDER_STATUS_CHANGED = "order_status_changed"
    ORDER_NOTES_UPDATED = "order_notes_updated"
    
    # Subscription
    SUBSCRIPTION_CREATED = "subscription_created"
    SUBSCRIPTION_UPDATED = "subscription_updated"
    SUBSCRIPTION_RENEWED = "subscription_renewed"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    
    # Security
    FAILED_AUTH_ATTEMPT = "failed_auth_attempt"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    CSRF_FAILURE = "csrf_failure"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"


async def audit_log(
    action: AuditAction,
    actor_id: str,
    actor_role: str,
    target_id: Optional[str] = None,
    target_type: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    request: Optional[Any] = None,
    success: bool = True,
    error_message: Optional[str] = None,
) -> None:
    """
    Log an audit event.
    
    Args:
        action: The action that was performed
        actor_id: ID of the user who performed the action
        actor_role: Role of the actor
        target_id: ID of the resource affected (optional)
        target_type: Type of resource affected (optional)
        details: Additional details about the action (optional)
        request: FastAPI Request object for IP/User-Agent (optional)
        success: Whether the action succeeded
        error_message: Error message if action failed
    """
    settings = get_settings()
    
    # Don't log in tests unless explicitly enabled
    if settings.LOG_LEVEL == "DEBUG":
        return
    
    entry = {
        "action": action.value,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "target_id": target_id,
        "target_type": target_type,
        "details": details or {},
        "success": success,
        "error_message": error_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    # Add request context if available
    if request:
        entry["ip"] = _get_client_ip(request)
        entry["user_agent"] = request.headers.get("User-Agent", "")[:200]
        entry["path"] = str(request.url.path)
    
    try:
        await db["audit_logs"].insert_one(entry)
    except Exception:
        # Don't let audit logging failures break the main flow
        pass


def _get_client_ip(request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if hasattr(request, "client") and request.client:
        return request.client.host
    return "unknown"


async def get_audit_logs(
    actor_id: Optional[str] = None,
    action: Optional[AuditAction] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    skip: int = 0,
) -> list:
    """Query audit logs with filters."""
    query = {}
    if actor_id:
        query["actor_id"] = actor_id
    if action:
        query["action"] = action.value
    if start_date or end_date:
        date_query = {}
        if start_date:
            date_query["$gte"] = start_date.isoformat()
        if end_date:
            date_query["$lte"] = end_date.isoformat()
        query["timestamp"] = date_query
    
    cursor = db["audit_logs"].find(query).sort("timestamp", -1).skip(skip).limit(limit)
    return await cursor.to_list(limit)


async def get_failed_login_attempts(user_id: str, since: datetime) -> int:
    """Count failed login attempts for a user since a given time."""
    return await db["audit_logs"].count_documents({
        "action": AuditAction.LOGIN_FAILED.value,
        "actor_id": user_id,
        "timestamp": {"$gte": since.isoformat()},
    })