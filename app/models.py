import re
import secrets
from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field, field_validator

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

USER_STATUS = Literal["pending", "active", "suspended"]
CARD_STATUS = Literal["pending", "active", "frozen"]


def gen_activation_token() -> str:
    """Generate cryptographically secure activation token."""
    return secrets.token_urlsafe(32)


def validate_password_strength(password: str) -> None:
    """Validate password meets complexity requirements."""
    errors = []
    if len(password) < 12:
        errors.append("at least 12 characters")
    if not re.search(r"[A-Z]", password):
        errors.append("one uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("one lowercase letter")
    if not re.search(r"[0-9]", password):
        errors.append("one number")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]", password):
        errors.append("one special character")
    if errors:
        raise ValueError(f"Password must contain: {', '.join(errors)}")


class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str = Field(..., min_length=12, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("username")
    @classmethod
    def _v(cls, v: str) -> str:
        v = v.strip().lower()
        if not USERNAME_RE.match(v):
            raise ValueError("username must be lowercase, start with letter/underscore, 1-32 chars [a-z0-9_-]")
        return v

    @field_validator("password")
    @classmethod
    def _v_password(cls, v: str) -> str:
        validate_password_strength(v)
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None


class PasswordReset(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    password: str = Field(..., min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def _v_password(cls, v: str) -> str:
        validate_password_strength(v)
        return v


# --- Cards ---

class CardCreate(BaseModel):
    card_type: Literal["social", "corporate", "taglink"]
    card_tier: Literal["digital", "physical"] = "digital"
    vcard_data: Optional[str] = None
    redirect_url: Optional[str] = None
    plain_text: Optional[str] = None
    template_id: Optional[str] = None
    template_fields: Optional[dict] = None


class AdminCardCreate(BaseModel):
    """Admin creates a card and associated user account in one flow."""
    card_type: Literal["social", "corporate", "taglink"]
    card_tier: Literal["digital", "physical"] = "physical"
    vcard_data: Optional[str] = None
    redirect_url: Optional[str] = None
    plain_text: Optional[str] = None
    template_id: Optional[str] = None
    template_fields: Optional[dict] = None
    # User details
    user_email: EmailStr
    user_display_name: str = Field(..., min_length=1, max_length=100)
    user_username: str = Field(..., min_length=1, max_length=32)
    user_role: Literal["individual", "corporate_admin"] = "individual"

    @field_validator("user_username")
    @classmethod
    def _v_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not USERNAME_RE.match(v):
            raise ValueError("username must be lowercase, start with letter/underscore, 1-32 chars [a-z0-9_-]")
        return v


class CardUpdate(BaseModel):
    vcard_data: Optional[str] = None
    redirect_url: Optional[str] = None
    plain_text: Optional[str] = None


class CardFreeze(BaseModel):
    reason: str = Field("", max_length=500)


class CardActivate(BaseModel):
    pin: str = Field(..., min_length=1, max_length=32)


class CardConvert(BaseModel):
    target_type: Literal["social", "taglink"]
    target_tier: Literal["digital", "physical"] = "digital"


# --- Orders ---

class OrderCreate(BaseModel):
    card_type: Literal["social", "corporate", "taglink"]
    card_tier: Literal["digital", "physical"] = "digital"
    material: Optional[Literal["plastic", "aluminium"]] = None
    shipping_address: Optional[dict] = None
    notes: Optional[str] = None
    card_id: Optional[str] = None  # Link to existing admin-created card


# --- Subscriptions ---

class SubscriptionCreate(BaseModel):
    card_id: str
    plan: Literal["social_yearly", "corporate_yearly", "taglink_monthly"]
    manual: bool = False


# --- TagLink ---

class TagLinkUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)


class TagLinkKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


# --- Admin ---

class AdminUserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str
    role: Literal["individual", "corporate_admin", "admin", "logistics_admin"] = "individual"


class AdminUserUpdate(BaseModel):
    display_name: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[Literal["individual", "corporate_admin", "admin", "logistics_admin"]] = None
    status: Optional[USER_STATUS] = None


# --- Corporate ---

class CorporateMemberInvite(BaseModel):
    email: EmailStr
    display_name: str = Field(..., min_length=1, max_length=100)


class OrgSettingsUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    slug: Optional[str] = Field(None, min_length=2, max_length=64)
    custom_domain: Optional[str] = Field(None, max_length=255)
    remove_domain: bool = False


class OrgDomainVerify(BaseModel):
    pass


# --- Activation ---

class UserActivate(BaseModel):
    """User activation with password setup."""
    token: str
    password: str = Field(..., min_length=8, max_length=128)
    password_confirm: str = Field(..., min_length=8, max_length=128)

    @field_validator("password_confirm")
    @classmethod
    def _v_match(cls, v: str, info) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("passwords do not match")
        return v


class ActivationToken(BaseModel):
    """Activation token data stored in card document."""
    token: str
    expires_at: datetime
    used: bool = False



