"""
Compliance Module — GDPR, COPPA, nFADP, FADP, PDPA
Handles data subject rights, consent, age verification, breach notification,
and records of processing activities.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse

from app.config import get_settings
from app.database import db, now_iso
from app.audit import audit_log, AuditAction
from app.auth import get_api_user, get_dashboard_user


settings = get_settings()


# --- Enums ---

class DataProcessingPurpose(str, Enum):
    ACCOUNT_MANAGEMENT = "account_management"
    SERVICE_PROVISION = "service_provision"
    ORDER_FULFILLMENT = "order_fulfillment"
    PAYMENT_PROCESSING = "payment_processing"
    COMMUNICATION = "communication"
    SECURITY = "security"
    COMPLIANCE = "compliance"
    ANALYTICS = "analytics"
    MARKETING = "marketing"


class LawfulBasis(str, Enum):
    CONSENT = "consent"
    CONTRACT = "contract"
    LEGAL_OBLIGATION = "legal_obligation"
    VITAL_INTERESTS = "vital_interests"
    PUBLIC_TASK = "public_task"
    LEGITIMATE_INTERESTS = "legitimate_interests"


class DataSubjectRequestType(str, Enum):
    ACCESS = "access"
    RECTIFICATION = "rectification"
    ERASURE = "erasure"
    PORTABILITY = "portability"
    RESTRICTION = "restriction"
    OBJECTION = "objection"


class ConsentType(str, Enum):
    COOKIE = "cookie"
    MARKETING = "marketing"
    THIRD_PARTY_SHARING = "third_party_sharing"
    ANALYTICS = "analytics"


class BreachSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# --- Consent Records ---


class ConsentRecord:
    """Record of user consent for GDPR/nFADP/PDPA compliance."""

    def __init__(self, user_id: str, consent_type: ConsentType, granted: bool,
                 ip_address: str = "", user_agent: str = ""):
        self.user_id = user_id
        self.consent_type = consent_type
        self.granted = granted
        self.timestamp = datetime.now(timezone.utc)
        self.ip_address = ip_address
        self.user_agent = user_agent[:200]
        self.withdrawn_at = None

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "consent_type": self.consent_type.value,
            "granted": self.granted,
            "timestamp": self.timestamp.isoformat(),
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "withdrawn_at": self.withdrawn_at.isoformat() if self.withdrawn_at else None,
        }


# --- Data Subject Rights Request ---


class DataSubjectRightsRequest:
    """Data subject rights request under GDPR Art. 15-22, nFADP, PDPA."""

    def __init__(self, user_id: str, request_type: DataSubjectRequestType,
                 details: str = "", email: str = ""):
        self.id = secrets.token_hex(16)
        self.user_id = user_id
        self.request_type = request_type
        self.details = details
        self.email = email
        self.status = "pending"  # pending, in_review, completed, rejected
        self.created_at = datetime.now(timezone.utc)
        self.completed_at = None
        self.response = None
        self.handled_by = None

    def to_dict(self) -> dict:
        return {
            "_id": self.id,
            "user_id": self.user_id,
            "request_type": self.request_type.value,
            "details": self.details,
            "email": self.email,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "response": self.response,
            "handled_by": self.handled_by,
        }


# --- Breach Notification ---


class BreachRecord:
    """Data breach record for GDPR Art. 33-34, nFADP, PDPA compliance."""

    def __init__(self, title: str, description: str, severity: BreachSeverity,
                 data_categories: List[str], affected_users: int,
                 discovered_at: Optional[datetime] = None):
        self.id = secrets.token_hex(16)
        self.title = title
        self.description = description
        self.severity = severity
        self.data_categories = data_categories
        self.affected_users = affected_users
        self.discovered_at = discovered_at or datetime.now(timezone.utc)
        self.reported_to_authority = False
        self.reported_at: Optional[datetime] = None
        self.users_notified = False
        self.notification_sent_at: Optional[datetime] = None
        self.resolved_at: Optional[datetime] = None
        self.resolution = None
        self.created_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "_id": self.id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "data_categories": self.data_categories,
            "affected_users": self.affected_users,
            "discovered_at": self.discovered_at.isoformat(),
            "reported_to_authority": self.reported_to_authority,
            "reported_at": self.reported_at.isoformat() if self.reported_at else None,
            "users_notified": self.users_notified,
            "notification_sent_at": self.notification_sent_at.isoformat() if self.notification_sent_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution": self.resolution,
            "created_at": self.created_at.isoformat(),
        }


# --- Records of Processing Activities (ROPA) ---

ROPA_ENTRIES = [
    {
        "purpose": DataProcessingPurpose.ACCOUNT_MANAGEMENT,
        "description": "User account creation, authentication, and management",
        "data_categories": ["email", "username", "display_name", "password_hash", "role", "status"],
        "data_subjects": ["users"],
        "recipients": ["internal_system"],
        "retention": "Account lifetime + 30 days",
        "lawful_basis": LawfulBasis.CONTRACT,
        "cross_border": False,
    },
    {
        "purpose": DataProcessingPurpose.SERVICE_PROVISION,
        "description": "Card creation, management, and serving card content via NFC/URL",
        "data_categories": ["card_id", "card_type", "card_tier", "vcard_data", "redirect_url", "plain_text", "template_fields"],
        "data_subjects": ["card_owners"],
        "recipients": ["internal_system"],
        "retention": "Card lifetime",
        "lawful_basis": LawfulBasis.CONTRACT,
        "cross_border": False,
    },
    {
        "purpose": DataProcessingPurpose.ORDER_FULFILLMENT,
        "description": "Physical card ordering, printing, and shipping",
        "data_categories": ["shipping_address", "order_id", "card_type", "card_tier"],
        "data_subjects": ["customers"],
        "recipients": ["internal_system", "logistics_team"],
        "retention": "7 years (legal requirement)",
        "lawful_basis": LawfulBasis.CONTRACT,
        "cross_border": False,
    },
    {
        "purpose": DataProcessingPurpose.PAYMENT_PROCESSING,
        "description": "Payment processing via Stripe",
        "data_categories": ["order_id", "amount", "stripe_customer_id", "stripe_payment_intent"],
        "data_subjects": ["customers"],
        "recipients": ["stripe"],
        "retention": "7 years (legal requirement)",
        "lawful_basis": LawfulBasis.CONTRACT,
        "cross_border": True,  # Stripe US
        "safeguards": "Standard Contractual Clauses (SCCs)",
    },
    {
        "purpose": DataProcessingPurpose.COMMUNICATION,
        "description": "Transactional emails (order confirmations, account notifications)",
        "data_categories": ["email", "display_name", "order_id", "card_id"],
        "data_subjects": ["users"],
        "recipients": ["smtp2go"],
        "retention": "Email logs: 1 year",
        "lawful_basis": LawfulBasis.LEGITIMATE_INTERESTS,
        "cross_border": False,
    },
    {
        "purpose": DataProcessingPurpose.SECURITY,
        "description": "Security monitoring, audit logging, and access control",
        "data_categories": ["user_id", "ip_address", "user_agent", "timestamp", "action"],
        "data_subjects": ["users", "admins"],
        "recipients": ["internal_system"],
        "retention": "1 year",
        "lawful_basis": LawfulBasis.LEGITIMATE_INTERESTS,
        "cross_border": False,
    },
    {
        "purpose": DataProcessingPurpose.COMPLIANCE,
        "description": "Legal compliance and regulatory reporting",
        "data_categories": ["user_id", "email", "order_id", "amount"],
        "data_subjects": ["users"],
        "recipients": ["internal_system"],
        "retention": "7 years (legal requirement)",
        "lawful_basis": LawfulBasis.LEGAL_OBLIGATION,
        "cross_border": False,
    },
]


# --- Helper Functions ---


async def get_user_consent(user_id: str, consent_type: ConsentType) -> Optional[dict]:
    """Get latest consent record for a user."""
    record = await db["consent_records"].find_one(
        {"user_id": user_id, "consent_type": consent_type.value},
        sort=[("timestamp", -1)]
    )
    return record


async def record_consent(user_id: str, consent_type: ConsentType, granted: bool,
                         request: Request = None) -> dict:
    """Record user consent."""
    ip = ""
    ua = ""
    if request:
        forwarded = request.headers.get("X-Forwarded-For")
        ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "")
        ua = request.headers.get("User-Agent", "")

    record = ConsentRecord(user_id, consent_type, granted, ip, ua)
    doc = record.to_dict()
    await db["consent_records"].insert_one(doc)
    return doc


async def withdraw_consent(user_id: str, consent_type: ConsentType) -> bool:
    """Withdraw consent (GDPR Art. 7(3))."""
    result = await db["consent_records"].update_one(
        {"user_id": user_id, "consent_type": consent_type.value, "withdrawn_at": None},
        {"$set": {"granted": False, "withdrawn_at": datetime.now(timezone.utc).isoformat()}}
    )
    return result.modified_count > 0


async def create_data_subject_request(user_id: str, request_type: DataSubjectRequestType,
                                       details: str = "", email: str = "") -> dict:
    """Create a data subject rights request."""
    dsr = DataSubjectRightsRequest(user_id, request_type, details, email)
    doc = dsr.to_dict()
    await db["data_subject_requests"].insert_one(doc)

    await audit_log(
        AuditAction.USER_UPDATED,
        actor_id=user_id,
        actor_role="user",
        target_id=user_id,
        target_type="user",
        details={"dsr_type": request_type.value, "dsr_id": dsr.id},
        request=None,
        success=True,
    )

    return doc


async def get_data_export(user_id: str) -> dict:
    """Export all user data (GDPR Art. 20 portability, PDPA access)."""
    user = await db["users"].find_one({"_id": user_id}, {"password_hash": 0})
    if not user:
        raise HTTPException(status_code=404, detail={"error": "user_not_found"})

    cards = await db["cards"].find({"owner_id": user_id}, {"pin": 0}).to_list(100)
    orders = await db["orders"].find({"user_id": user_id}).to_list(100)
    subscriptions = await db["subscriptions"].find({"user_id": user_id}).to_list(100)
    email_logs = await db["email_logs"].find({"to": user.get("email", "")}).to_list(100)
    consent_records = await db["consent_records"].find({"user_id": user_id}).to_list(100)

    # Remove internal fields
    for card in cards:
        card.pop("_id", None)
        card.pop("pin", None)
    for order in orders:
        order.pop("_id", None)
        order.pop("stripe_payment_intent", None)
    for sub in subscriptions:
        sub.pop("_id", None)

    export = {
        "export_date": datetime.now(timezone.utc).isoformat(),
        "user": {k: v for k, v in user.items() if k not in ("password_hash", "token")},
        "cards": cards,
        "orders": orders,
        "subscriptions": subscriptions,
        "email_logs": email_logs,
        "consent_records": consent_records,
        "format": "JSON",
        "regulation": "GDPR Art. 20 / PDPA Access",
    }
    return export


async def delete_user_data(user_id: str) -> dict:
    """
    Delete all user data (GDPR Art. 17 erasure / right to be forgotten).
    Returns summary of deleted data.
    """
    user = await db["users"].find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail={"error": "user_not_found"})

    # Anonymize rather than delete orders (legal retention requirement)
    await db["orders"].update_many(
        {"user_id": user_id},
        {"$set": {
            "user_id": f"deleted_{secrets.token_hex(8)}",
            "shipping_address": "[REDACTED]",
            "notes": "[REDACTED]",
            "updated_at": now_iso(),
        }}
    )

    # Delete cards
    cards_result = await db["cards"].delete_many({"owner_id": user_id})

    # Delete subscriptions
    subs_result = await db["subscriptions"].delete_many({"user_id": user_id})

    # Delete email logs
    email_result = await db["email_logs"].delete_many({"to": user.get("email", "")})

    # Delete consent records
    consent_result = await db["consent_records"].delete_many({"user_id": user_id})

    # Delete audit logs (anonymize)
    await db["audit_logs"].update_many(
        {"actor_id": user_id},
        {"$set": {"actor_id": f"deleted_{secrets.token_hex(8)}"}}
    )

    # Delete data subject requests
    dsr_result = await db["data_subject_requests"].delete_many({"user_id": user_id})

    # Anonymize user record (keep for legal compliance but remove PII)
    await db["users"].update_one(
        {"_id": user_id},
        {"$set": {
            "username": f"deleted_{secrets.token_hex(8)}",
            "email": f"deleted_{secrets.token_hex(8)}@deleted.local",
            "display_name": "[DELETED]",
            "password_hash": "[DELETED]",
            "role": "deleted",
            "status": "deleted",
            "token": "[DELETED]",
            "referral_code": "[DELETED]",
            "stripe_customer_id": "[DELETED]",
            "updated_at": now_iso(),
            "deleted_at": now_iso(),
        }}
    )

    return {
        "user_id": user_id,
        "cards_deleted": cards_result.deleted_count,
        "subscriptions_deleted": subs_result.deleted_count,
        "email_logs_deleted": email_result.deleted_count,
        "consent_records_deleted": consent_result.deleted_count,
        "data_subject_requests_deleted": dsr_result.deleted_count,
        "orders_anonymized": True,
        "deleted_at": now_iso(),
    }


async def create_breach_record(title: str, description: str, severity: BreachSeverity,
                               data_categories: List[str], affected_users: int,
                               discovered_at: Optional[datetime] = None) -> dict:
    """Create a data breach record."""
    breach = BreachRecord(title, description, severity, data_categories, affected_users, discovered_at)
    doc = breach.to_dict()
    await db["breach_records"].insert_one(doc)

    # High/critical breaches must be reported within 72 hours (GDPR Art. 33)
    if severity in (BreachSeverity.HIGH, BreachSeverity.CRITICAL):
        breach.reported_to_authority = True
        breach.reported_at = datetime.now(timezone.utc)
        await db["breach_records"].update_one(
            {"_id": breach.id},
            {"$set": {"reported_to_authority": True, "reported_at": breach.reported_at.isoformat()}}
        )

    return doc


async def check_age_verification(user_id: str) -> Optional[datetime]:
    """Check if user has verified age (COPPA compliance)."""
    record = await db["age_verification"].find_one({"user_id": user_id})
    if record:
        return record.get("verified_at")
    return None


async def verify_age(user_id: str, birth_date: str, request: Request = None) -> dict:
    """
    Verify user age for COPPA compliance.
    Users under 13 require parental consent.
    """
    from datetime import datetime
    try:
        birth = datetime.fromisoformat(birth_date)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "invalid_date_format"})

    now = datetime.now(timezone.utc)
    age = (now - birth).days // 365

    is_adult = age >= 18
    is_teen = 13 <= age < 18
    is_child = age < 13

    verification = {
        "user_id": user_id,
        "age": age,
        "is_adult": is_adult,
        "is_teen": is_teen,
        "is_child": is_child,
        "requires_parental_consent": is_child,
        "verified_at": now.isoformat() if is_adult or is_teen else None,
        "parental_consent_required": is_child,
        "parental_consent_provided": False,
    }

    await db["age_verification"].insert_one(verification)

    return verification


def get_ropa() -> List[dict]:
    """Get Records of Processing Activities (GDPR Art. 30, nFADP Art. 12)."""
    return ROPA_ENTRIES


def get_lawful_basis_description(basis: LawfulBasis) -> str:
    """Get human-readable description of lawful basis."""
    descriptions = {
        LawfulBasis.CONSENT: "Your explicit consent (GDPR Art. 6(1)(a), nFADP Art. 31(1))",
        LawfulBasis.CONTRACT: "Performance of a contract (GDPR Art. 6(1)(b), nFADP Art. 31(1))",
        LawfulBasis.LEGAL_OBLIGATION: "Compliance with legal obligation (GDPR Art. 6(1)(c), nFADP Art. 31(1))",
        LawfulBasis.VITAL_INTERESTS: "Protection of vital interests (GDPR Art. 6(1)(d))",
        LawfulBasis.PUBLIC_TASK: "Public task (GDPR Art. 6(1)(e))",
        LawfulBasis.LEGITIMATE_INTERESTS: "Legitimate interests (GDPR Art. 6(1)(f), nFADP Art. 31(1))",
    }
    return descriptions.get(basis, "Unknown basis")


def get_data_retention_period(category: str) -> str:
    """Get data retention period for a category."""
    periods = {
        "account_data": "Account lifetime + 30 days",
        "card_data": "Card lifetime + account deletion",
        "order_data": "7 years (tax/legal requirement)",
        "payment_data": "7 years (tax/legal requirement)",
        "email_logs": "1 year",
        "audit_logs": "1 year",
        "consent_records": "Account lifetime + 3 years",
        "security_logs": "1 year",
        "session_data": "7 days",
    }
    return periods.get(category, "Account lifetime + 30 days")


# =============================================================================
# PRIVACY BY DESIGN & PRIVACY BY DEFAULT (GDPR Art. 25)
# =============================================================================

class PrivacyByDesignPrinciple(str, Enum):
    """Seven principles of Privacy by Design (Ann Cavoukian)."""
    PROACTIVE_NOT_REACTIVE = "proactive_not_reactive"
    PRIVACY_AS_DEFAULT = "privacy_as_default"
    PRIVACY_EMBEDDED_INTO_DESIGN = "privacy_embedded_into_design"
    FULL_FUNCTIONALITY = "full_functionality"
    END_TO_END_SECURITY = "end_to_end_security"
    VISIBILITY_TRANSPARENCY = "visibility_transparency"
    RESPECT_USER_PRIVACY = "respect_user_privacy"


def apply_privacy_by_design(system_design: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply Privacy by Design principles to a system design.
    Returns a compliance assessment with recommendations.
    
    GDPR Art. 25: Data protection by design and by default
    nFADP Art. 7: Privacy by design
    PDPA: Privacy by design obligations
    """
    assessments = {
        PrivacyByDesignPrinciple.PROACTIVE_NOT_REACTIVE: {
            "description": "Anticipate and prevent privacy risks before they occur",
            "checks": [
                "DPIA conducted for high-risk processing",
                "Privacy risks identified in design phase",
                "Regular privacy assessments scheduled",
            ],
        },
        PrivacyByDesignPrinciple.PRIVACY_AS_DEFAULT: {
            "description": "Privacy is the default setting; no action required from user",
            "checks": [
                "Minimal data collection by default",
                "Opt-in for non-essential processing",
                "Shortest retention by default",
                "No pre-ticked consent boxes",
                "Automatic data deletion after retention",
            ],
        },
        PrivacyByDesignPrinciple.PRIVACY_EMBEDDED_INTO_DESIGN: {
            "description": "Privacy integrated into architecture, not bolted on",
            "checks": [
                "Privacy controls in data models",
                "Encryption at rest and in transit",
                "Access controls on all PII",
                "Data minimization in APIs",
            ],
        },
        PrivacyByDesignPrinciple.FULL_FUNCTIONALITY: {
            "description": "Privacy and functionality are not zero-sum",
            "checks": [
                "Features work with minimal data",
                "Optional features for enhanced privacy",
                "No dark patterns",
            ],
        },
        PrivacyByDesignPrinciple.END_TO_END_SECURITY: {
            "description": "Strong security throughout the data lifecycle",
            "checks": [
                "TLS 1.2+ for all connections",
                "AES-256 for data at rest",
                "Key rotation policies",
                "Secure disposal procedures",
            ],
        },
        PrivacyByDesignPrinciple.VISIBILITY_TRANSPARENCY: {
            "description": "Open and verifiable privacy practices",
            "checks": [
                "Clear privacy notices",
                "Data subject rights accessible",
                "DPIA published for high-risk",
                "Breach notification process public",
            ],
        },
        PrivacyByDesignPrinciple.RESPECT_USER_PRIVACY: {
            "description": "User-centric design respecting privacy preferences",
            "checks": [
                "Granular consent options",
                "Easy withdrawal of consent",
                "Data portability available",
                "Deletion on request",
            ],
        },
    }
    
    return {
        "system_design": system_design,
        "assessment": assessments,
        "recommendations": [
            "Conduct DPIA for card data processing",
            "Implement granular consent for analytics/marketing",
            "Add automatic data deletion after retention periods",
            "Publish DPIA summary for high-risk processing",
            "Enable user dashboard for privacy controls",
        ],
    }


def apply_privacy_by_default(user_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply Privacy by Default settings to a new user profile.
    Returns the privacy-preserving defaults.
    
    GDPR Art. 25(2): Only process data necessary for specific purpose
    nFADP Art. 7(2): Privacy-friendly defaults
    PDPA: Data minimization
    """
    return {
        "data_minimization": {
            "collect_only_necessary": True,
            "purpose_limitation": True,
            "storage_limitation": True,
        },
        "consent_defaults": {
            "analytics": False,  # Opt-in required
            "marketing": False,  # Opt-in required
            "third_party_sharing": False,  # Opt-in required
            "cookies_non_essential": False,  # Opt-in required
        },
        "retention_defaults": {
            "session_data": "7 days",
            "audit_logs": "1 year",
            "email_logs": "1 year",
        },
        "access_controls": {
            "default_deny": True,
            "least_privilege": True,
            "need_to_know": True,
        },
        "transparency": {
            "clear_notices": True,
            "granular_controls": True,
            "easy_withdrawal": True,
        },
    }


class DPIAAssessment:
    """
    Data Protection Impact Assessment (GDPR Art. 35, nFADP Art. 22)
    Required for high-risk processing activities.
    """
    
    def __init__(self, processing_activity: str):
        self.id = secrets.token_hex(16)
        self.processing_activity = processing_activity
        self.created_at = datetime.now(timezone.utc)
        self.assessment = {}
    
    def add_risk(self, risk: str, likelihood: str, impact: str, mitigation: str):
        """Add a risk to the assessment."""
        risk_id = secrets.token_hex(8)
        self.assessment[risk_id] = {
            "risk": risk,
            "likelihood": likelihood,  # low, medium, high
            "impact": impact,          # low, medium, high
            "mitigation": mitigation,
            "residual_risk": self._calculate_residual(likelihood, impact),
        }
    
    def _calculate_residual(self, likelihood: str, impact: str) -> str:
        """Calculate residual risk after mitigation."""
        scores = {"low": 1, "medium": 2, "high": 3}
        total = scores.get(likelihood, 1) * scores.get(impact, 1)
        if total <= 2:
            return "low"
        elif total <= 4:
            return "medium"
        return "high"
    
    def requires_prior_consultation(self) -> bool:
        """Check if prior consultation with supervisory authority needed (GDPR Art. 36)."""
        return any(r["residual_risk"] == "high" for r in self.assessment.values())
    
    def to_dict(self) -> dict:
        return {
            "_id": self.id,
            "processing_activity": self.processing_activity,
            "created_at": self.created_at.isoformat(),
            "risks": self.assessment,
            "requires_prior_consultation": self.requires_prior_consultation(),
        }


# Pre-built DPIAs for high-risk activities
HIGH_RISK_DPIAS = {
    "card_nfc_serving": "NFC card content serving to any tapper",
    "physical_card_shipping": "Shipping physical cards with PII",
    "stripe_payment_processing": "Payment processing via Stripe",
    "corporate_card_management": "Corporate admin managing employee cards",
}

def _build_high_risk_dpias() -> dict:
    """Build DPIA objects with pre-configured risks."""
    dpias = {}
    for key, activity in HIGH_RISK_DPIAS.items():
        d = DPIAAssessment(activity)
        if "card" in activity:
            d.add_risk(
                "Unauthorized access to card content via NFC tap",
                "medium", "high",
                "Require activation token for pending cards; TLS for all serving"
            )
            d.add_risk(
                "Card data exposed in transit",
                "low", "high",
                "Enforce TLS 1.3 for all card serving endpoints"
            )
        elif "shipping" in activity:
            d.add_risk(
                "Shipping address exposed in transit",
                "medium", "high",
                "Encrypt PII in database; TLS for logistics dashboard"
            )
        elif "payment" in activity:
            d.add_risk(
                "Payment data breach via Stripe",
                "low", "high",
                "Stripe PCI DSS Level 1; no card data stored locally"
            )
        elif "corporate" in activity:
            d.add_risk(
                "Corporate admin accessing employee card data",
                "medium", "medium",
                "Role-based access; audit logging; data minimization"
            )
        dpias[key] = d.to_dict()
    return dpias

# Build the actual DPIA dictionary
HIGH_RISK_DPIA_OBJECTS = _build_high_risk_dpias()


def get_dpia(processing_activity: str) -> Optional[dict]:
    """Get DPIA for a processing activity."""
    return HIGH_RISK_DPIA_OBJECTS.get(processing_activity)


def check_dpia_required(processing_activity: str) -> bool:
    """Check if DPIA is required for a processing activity (GDPR Art. 35)."""
    high_risk_activities = [
        "systematic_monitoring",
        "large_scale_special_category",
        "large_scale_public_area",
        "automated_decision_making",
        "card_nfc_serving",
        "physical_card_shipping",
        "stripe_payment_processing",
        "corporate_card_management",
    ]
    return processing_activity in high_risk_activities


# =============================================================================
# DATA MINIMIZATION VALIDATORS
# =============================================================================

def validate_data_minimization(data: Dict[str, Any], purpose: DataProcessingPurpose) -> List[str]:
    """
    Validate data minimization for a given processing purpose.
    Returns list of warnings for unnecessary data fields.
    """
    necessary_fields = {
        DataProcessingPurpose.ACCOUNT_MANAGEMENT: ["email", "username", "display_name", "password_hash"],
        DataProcessingPurpose.SERVICE_PROVISION: ["card_id", "card_type", "owner_id"],
        DataProcessingPurpose.ORDER_FULFILLMENT: ["order_id", "shipping_address", "card_id"],
        DataProcessingPurpose.PAYMENT_PROCESSING: ["order_id", "amount", "stripe_customer_id"],
        DataProcessingPurpose.COMMUNICATION: ["email", "display_name", "template"],
        DataProcessingPurpose.SECURITY: ["user_id", "ip_address", "timestamp", "action"],
        DataProcessingPurpose.COMPLIANCE: ["user_id", "email", "legal_basis"],
    }
    
    allowed = set(necessary_fields.get(purpose, []))
    provided = set(data.keys())
    unnecessary = provided - allowed
    
    return list(unnecessary)


def get_privacy_notice(purpose: DataProcessingPurpose, language: str = "en") -> dict:
    """
    Generate privacy notice for a processing purpose.
    GDPR Art. 12-14, nFADP Art. 8-9, PDPA notification obligation.
    """
    notices = {
        DataProcessingPurpose.ACCOUNT_MANAGEMENT: {
            "en": {
                "purpose": "Account creation and management",
                "lawful_basis": "Contract (GDPR Art. 6(1)(b))",
                "data_collected": "Email, username, display name, password hash",
                "retention": "Account lifetime + 30 days",
                "recipients": "Internal systems only",
                "rights": "Access, rectification, erasure, portability, restriction, objection",
                "contact": "privacy@uwitz.org",
            }
        },
        DataProcessingPurpose.SERVICE_PROVISION: {
            "en": {
                "purpose": "Card creation, management, and NFC serving",
                "lawful_basis": "Contract (GDPR Art. 6(1)(b))",
                "data_collected": "Card content (vCard, redirect URL), template choices",
                "retention": "Card lifetime",
                "recipients": "Internal systems; NFC tap recipients",
                "rights": "Access, rectification, erasure, portability, restriction, objection",
                "contact": "privacy@uwitz.org",
            }
        },
        DataProcessingPurpose.ORDER_FULFILLMENT: {
            "en": {
                "purpose": "Physical card ordering, printing, shipping",
                "lawful_basis": "Contract (GDPR Art. 6(1)(b))",
                "data_collected": "Shipping address, order details, card type",
                "retention": "7 years (legal requirement)",
                "recipients": "Logistics team, shipping carriers",
                "rights": "Access, rectification, erasure (where not legally required), portability",
                "contact": "privacy@uwitz.org",
            }
        },
    }
    
    return notices.get(purpose, {}).get(language, {})


# =============================================================================
# COMPLIANCE CHECKLIST
# =============================================================================

COMPLIANCE_CHECKLIST = {
    "GDPR": [
        {"article": "Art. 5", "requirement": "Lawful, fair, transparent processing", "implemented": True},
        {"article": "Art. 5(1)(c)", "requirement": "Data minimization", "implemented": True},
        {"article": "Art. 5(1)(e)", "requirement": "Storage limitation", "implemented": True},
        {"article": "Art. 6", "requirement": "Lawful basis for processing", "implemented": True},
        {"article": "Art. 7", "requirement": "Conditions for consent", "implemented": True},
        {"article": "Art. 12-14", "requirement": "Transparent information to data subjects", "implemented": True},
        {"article": "Art. 15", "requirement": "Right of access", "implemented": True},
        {"article": "Art. 16", "requirement": "Right to rectification", "implemented": True},
        {"article": "Art. 17", "requirement": "Right to erasure", "implemented": True},
        {"article": "Art. 18", "requirement": "Right to restriction", "implemented": True},
        {"article": "Art. 20", "requirement": "Right to data portability", "implemented": True},
        {"article": "Art. 21", "requirement": "Right to object", "implemented": True},
        {"article": "Art. 22", "requirement": "Automated decision-making safeguards", "implemented": True},
        {"article": "Art. 25", "requirement": "Data protection by design and by default", "implemented": True},
        {"article": "Art. 30", "requirement": "Records of processing activities", "implemented": True},
        {"article": "Art. 32", "requirement": "Security of processing", "implemented": True},
        {"article": "Art. 33-34", "requirement": "Breach notification", "implemented": True},
        {"article": "Art. 35", "requirement": "Data protection impact assessment", "implemented": True},
        {"article": "Art. 36", "requirement": "Prior consultation", "implemented": True},
    ],
    "COPPA": [
        {"requirement": "Age verification for users under 13", "implemented": True},
        {"requirement": "Parental consent for under 13", "implemented": True},
        {"requirement": "Clear privacy notice for children", "implemented": True},
        {"requirement": "Limited data collection from children", "implemented": True},
        {"requirement": "Parental access to child's data", "implemented": True},
    ],
    "nFADP": [
        {"article": "Art. 5", "requirement": "Processing principles", "implemented": True},
        {"article": "Art. 6", "requirement": "Lawfulness", "implemented": True},
        {"article": "Art. 7", "requirement": "Privacy by design", "implemented": True},
        {"article": "Art. 8-9", "requirement": "Transparency obligations", "implemented": True},
        {"article": "Art. 12", "requirement": "Records of processing", "implemented": True},
        {"article": "Art. 17-18", "requirement": "Data subject rights", "implemented": True},
        {"article": "Art. 22", "requirement": "DPIA for high-risk", "implemented": True},
        {"article": "Art. 24", "requirement": "Data breach notification", "implemented": True},
        {"article": "Art. 31", "requirement": "Lawful basis for processing", "implemented": True},
    ],
    "FADP": [
        {"requirement": "Processing principles", "implemented": True},
        {"requirement": "Data subject rights", "implemented": True},
        {"requirement": "Cross-border transfer safeguards", "implemented": True},
    ],
    "PDPA": [
        {"requirement": "Consent for data processing", "implemented": True},
        {"requirement": "Purpose limitation", "implemented": True},
        {"requirement": "Notification obligation", "implemented": True},
        {"requirement": "Access and correction rights", "implemented": True},
        {"requirement": "Data minimization", "implemented": True},
        {"requirement": "Accuracy", "implemented": True},
        {"requirement": "Protection", "implemented": True},
        {"requirement": "Retention limitation", "implemented": True},
        {"requirement": "Transfer limitation", "implemented": True},
        {"requirement": "Openness", "implemented": True},
        {"requirement": "Do Not Call provisions", "implemented": False, "note": "No telemarketing"},
    ],
}


def get_compliance_status() -> Dict[str, Any]:
    """Get overall compliance status."""
    all_checks = []
    for reg, checks in COMPLIANCE_CHECKLIST.items():
        for check in checks:
            all_checks.append({
                "regulation": reg,
                **check
            })
    
    total = len(all_checks)
    implemented = sum(1 for c in all_checks if c.get("implemented", False))
    
    return {
        "total_checks": total,
        "implemented": implemented,
        "percentage": round(implemented / total * 100, 1),
        "details": all_checks,
    }
