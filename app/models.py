import re
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field, field_validator

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("username")
    @classmethod
    def _v(cls, v: str) -> str:
        v = v.strip().lower()
        if not USERNAME_RE.match(v):
            raise ValueError("username must be lowercase, start with letter/underscore, 1-32 chars [a-z0-9_-]")
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
    password: str = Field(..., min_length=8, max_length=128)


# --- Cards ---

class CardCreate(BaseModel):
    card_type: Literal["social", "corporate", "taglink"]
    card_tier: Literal["digital", "physical"] = "digital"
    vcard_data: Optional[str] = None
    redirect_url: Optional[str] = None
    plain_text: Optional[str] = None


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
    shipping_address: Optional[dict] = None
    notes: Optional[str] = None


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
    status: Optional[Literal["active", "suspended"]] = None


# --- Corporate ---

class CorporateMemberInvite(BaseModel):
    email: EmailStr
    display_name: str = Field(..., min_length=1, max_length=100)
