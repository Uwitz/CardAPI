import re
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.IGNORECASE,
)


# --- Auth ---

class UserLogin(BaseModel):
    token: str = Field(..., min_length=1)


# --- User ---

class UserCreate(BaseModel):
    username: str
    display_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    plan: Literal["individual", "team", "enterprise"] = "individual"
    organisation: Optional[str] = None
    currency: str = "MYR"

    @field_validator("username")
    @classmethod
    def _v(cls, v: str) -> str:
        v = v.strip().lower()
        if not USERNAME_RE.match(v):
            raise ValueError("username must be lowercase, start with letter/underscore, 1-32 chars [a-z0-9_-]")
        return v


class UserResponse(BaseModel):
    id: str
    display_name: str
    email: Optional[str] = None
    plan: str
    plan_expiry: Optional[str] = None
    status: str = "active"
    username: str
    organisation: Optional[str] = None
    referral: Optional[str] = None
    referral_reward: float = 0.0
    currency: str = "MYR"
    payouts: list = Field(default_factory=list)
    transactions: list = Field(default_factory=list)
    is_admin: bool = False
    created_at: str
    updated_at: Optional[str] = None
    # NOTE: token is intentionally NOT included — security fix


# --- Card ---

class CardCreate(BaseModel):
    type: Literal["vcard", "url"]
    content: str
    owner_id: str
    tier: str = "plastic"
    status: Literal["active", "pending"] = "active"


class CardUpdate(BaseModel):
    type: Literal["vcard", "url"]
    content: str


class CardActivate(BaseModel):
    pin: str = Field(..., min_length=1, max_length=32)


class CardResponse(BaseModel):
    id: str
    tier: str
    type: str
    status: str
    views: int = 0
    owner_id: Optional[str] = None
    organisation: Optional[str] = None
    payment_id: Optional[str] = None
    version: Optional[float] = None
    content: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


# --- Order ---

class OrderCreate(BaseModel):
    tier: Literal["digital", "plastic", "premium"]
    card_type: Literal["vcard", "url"] = "vcard"
    card_content: str
    notes: Optional[str] = None


class OrderResponse(BaseModel):
    id: str
    user_id: str
    tier: str
    amount: float
    currency: str
    status: str
    card_id: Optional[str] = None
    payment_intent: Optional[str] = None
    checkout_url: Optional[str] = None
    created_at: str


# --- Payout ---

class PayoutRequest(BaseModel):
    amount: float = Field(..., gt=0)
    currency: str = "MYR"


class PayoutProcess(BaseModel):
    user_id: str
    payout_id: str
    action: Literal["approve", "reject"]
    reason: Optional[str] = Field(None, max_length=500)


# --- Domain Verification ---

class DomainCreate(BaseModel):
    """User submits a domain they want to verify via DNS TXT."""
    domain: str = Field(..., min_length=3, max_length=253)

    @field_validator("domain")
    @classmethod
    def _v(cls, v: str) -> str:
        v = v.strip().lower()
        # Strip protocol if user pasted a URL
        v = re.sub(r"^https?://", "", v)
        v = v.split("/")[0]  # strip path
        if not DOMAIN_RE.match(v):
            raise ValueError("invalid domain format")
        return v


class DomainResponse(BaseModel):
    id: str
    domain: str
    user_id: str
    verified: bool
    verification_token: str
    verification_host: str  # where the TXT record should be added
    verification_record: str  # the full TXT value to add
    verified_at: Optional[str] = None
    created_at: str
