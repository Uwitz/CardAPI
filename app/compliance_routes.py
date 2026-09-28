"""Compliance Routes — Data Subject Rights, Consent, Privacy Notices"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_api_user, get_dashboard_user, NotAuthenticated
from app.audit import audit_log, AuditAction
from app.compliance import (
    DataProcessingPurpose, LawfulBasis, ConsentType, DataSubjectRequestType, BreachSeverity,
    record_consent, withdraw_consent, get_user_consent, create_data_subject_request,
    get_data_export, delete_user_data, verify_age, check_age_verification,
    get_privacy_notice, get_compliance_status, get_dpia, check_dpia_required,
    get_lawful_basis_description, get_data_retention_period, COMPLIANCE_CHECKLIST,
    apply_privacy_by_default, apply_privacy_by_design,
    validate_data_minimization, create_breach_record,
)
from app.database import db, now_iso
from app.config import get_settings

settings = get_settings()

router = APIRouter(prefix="/api/compliance", tags=["compliance"])
templates = Jinja2Templates(directory="templates")


# --- Consent Endpoints ---

@router.post("/consent/{consent_type}")
async def give_consent(consent_type: ConsentType, request: Request, user: dict = Depends(get_api_user)):
    """Record user consent (GDPR Art. 7, nFADP Art. 31, PDPA)."""
    body = await request.json()
    granted = body.get("granted", True)
    
    if not granted:
        # Withdraw consent
        await withdraw_consent(user["_id"], consent_type)
        await audit_log(
            AuditAction.USER_UPDATED,
            actor_id=user["_id"],
            actor_role=user.get("role", "individual"),
            target_id=user["_id"],
            target_type="user",
            details={"consent_type": consent_type.value, "action": "withdrawn"},
            request=request,
            success=True,
        )
        return {"status": "consent_withdrawn", "consent_type": consent_type.value}
    
    # Give consent
    await record_consent(user["_id"], consent_type, granted, request)
    
    await audit_log(
        AuditAction.USER_UPDATED,
        actor_id=user["_id"],
        actor_role=user.get("role", "individual"),
        target_id=user["_id"],
        target_type="user",
        details={"consent_type": consent_type.value, "action": "granted"},
        request=request,
        success=True,
    )
    
    return {"status": "consent_recorded", "consent_type": consent_type.value, "granted": granted}


@router.get("/consent/{consent_type}")
async def get_consent(consent_type: ConsentType, user: dict = Depends(get_api_user)):
    """Get user's current consent status."""
    record = await get_user_consent(user["_id"], consent_type)
    if not record:
        return {"consent_type": consent_type.value, "granted": False, "recorded": False}
    
    return {
        "consent_type": consent_type.value,
        "granted": record.get("granted", False),
        "recorded": True,
        "timestamp": record.get("timestamp"),
        "withdrawn_at": record.get("withdrawn_at"),
    }


@router.get("/consent")
async def list_consents(user: dict = Depends(get_api_user)):
    """List all consent records for user."""
    consents = await db["consent_records"].find({"user_id": user["_id"]}).to_list(100)
    return {"consents": consents}


# --- Data Subject Rights Endpoints ---

@router.post("/data-subject-request")
async def submit_dsr(request: Request, user: dict = Depends(get_api_user)):
    """Submit a data subject rights request (GDPR Art. 15-22, nFADP, PDPA)."""
    body = await request.json()
    request_type = DataSubjectRequestType(body.get("type"))
    details = body.get("details", "")
    
    if request_type not in DataSubjectRequestType:
        raise HTTPException(status_code=400, detail={"error": "invalid_request_type"})
    
    dsr = await create_data_subject_request(user["_id"], request_type, details)
    
    return {
        "request_id": dsr["_id"],
        "type": dsr["request_type"],
        "status": dsr["status"],
        "created_at": dsr["created_at"],
        "message": "Request submitted. We will respond within 30 days as required by law.",
    }


@router.get("/data-subject-request")
async def list_dsr(user: dict = Depends(get_api_user)):
    """List user's data subject requests."""
    requests = await db["data_subject_requests"].find({"user_id": user["_id"]}).sort("created_at", -1).to_list(50)
    return {"requests": requests}


@router.get("/data-export")
async def export_user_data(user: dict = Depends(get_api_user)):
    """Export all user data (GDPR Art. 20, PDPA Access)."""
    export = await get_data_export(user["_id"])
    
    await audit_log(
        AuditAction.USER_UPDATED,
        actor_id=user["_id"],
        actor_role=user.get("role", "individual"),
        target_id=user["_id"],
        target_type="user",
        details={"action": "data_export"},
        request=None,
        success=True,
    )
    
    from fastapi.responses import Response
    return Response(
        content=json.dumps(export, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="uwitz-cards-data-export-{datetime.now(timezone.utc).strftime("%Y%m%d")}.json"'}
    )


@router.delete("/account")
async def delete_account(request: Request, user: dict = Depends(get_api_user)):
    """Delete account and all personal data (GDPR Art. 17, PDPA erasure)."""
    body = await request.json()
    confirmation = body.get("confirmation", "").lower()
    
    if confirmation != "delete my account":
        raise HTTPException(status_code=400, detail={"error": "confirmation_required"})
    
    result = await delete_user_data(user["_id"])
    
    await audit_log(
        AuditAction.USER_DELETED,
        actor_id=user["_id"],
        actor_role=user.get("role", "individual"),
        target_id=user["_id"],
        target_type="user",
        details={"action": "account_deletion", "summary": result},
        request=request,
        success=True,
    )
    
    return {
        "status": "account_deleted",
        "message": "Your account and personal data have been deleted. Some data may be retained for legal compliance.",
        "details": result,
    }


@router.post("/rectify")
async def rectify_data(request: Request, user: dict = Depends(get_api_user)):
    """Rectify inaccurate personal data (GDPR Art. 16)."""
    body = await request.json()
    corrections = body.get("corrections", {})
    
    if not corrections:
        raise HTTPException(status_code=400, detail={"error": "no_corrections_provided"})
    
    # Only allow certain fields to be corrected
    allowed_fields = ["display_name", "email"]
    updates = {}
    for field, value in corrections.items():
        if field in allowed_fields:
            updates[field] = value
    
    if not updates:
        raise HTTPException(status_code=400, detail={"error": "no_valid_fields"})
    
    updates["updated_at"] = now_iso()
    await db["users"].update_one({"_id": user["_id"]}, {"$set": updates})
    
    await audit_log(
        AuditAction.USER_UPDATED,
        actor_id=user["_id"],
        actor_role=user.get("role", "individual"),
        target_id=user["_id"],
        target_type="user",
        details={"action": "rectification", "fields": list(updates.keys())},
        request=request,
        success=True,
    )
    
    return {"status": "rectified", "updated_fields": list(updates.keys())}


@router.post("/restrict-processing")
async def restrict_processing(request: Request, user: dict = Depends(get_api_user)):
    """Request restriction of processing (GDPR Art. 18)."""
    body = await request.json()
    reason = body.get("reason", "")
    
    await create_data_subject_request(user["_id"], DataSubjectRequestType.RESTRICTION, reason)
    
    return {"status": "restriction_requested", "message": "Your request to restrict processing has been submitted."}


@router.post("/object-processing")
async def object_processing(request: Request, user: dict = Depends(get_api_user)):
    """Object to processing (GDPR Art. 21)."""
    body = await request.json()
    reason = body.get("reason", "")
    purpose = body.get("purpose")
    
    if not purpose:
        raise HTTPException(status_code=400, detail={"error": "purpose_required"})
    
    await create_data_subject_request(user["_id"], DataSubjectRequestType.OBJECTION, 
                                      f"Objection to {purpose}: {reason}")
    
    return {"status": "objection_submitted", "message": "Your objection has been recorded."}


# --- Age Verification (COPPA) ---

@router.post("/age-verification")
async def submit_age_verification(request: Request, user: dict = Depends(get_api_user)):
    """Submit age verification for COPPA compliance."""
    body = await request.json()
    birth_date = body.get("birth_date")
    
    if not birth_date:
        raise HTTPException(status_code=400, detail={"error": "birth_date_required"})
    
    # Check if already verified
    existing = await check_age_verification(user["_id"])
    if existing:
        return {"status": "already_verified", "verified_at": existing.isoformat()}
    
    result = await verify_age(user["_id"], birth_date, request)
    
    if result.get("requires_parental_consent"):
        return {
            "status": "parental_consent_required",
            "age": result["age"],
            "message": "Users under 13 require parental consent. Please contact privacy@uwitz.org.",
        }
    
    return {
        "status": "verified",
        "age": result["age"],
        "message": "Age verified successfully.",
    }


@router.get("/age-verification")
async def get_age_verification(user: dict = Depends(get_api_user)):
    """Get age verification status."""
    verified_at = await check_age_verification(user["_id"])
    if not verified_at:
        return {"verified": False}
    
    return {"verified": True, "verified_at": verified_at.isoformat()}


# --- Privacy Notices & DPIAs ---

@router.get("/privacy-notice/{purpose}")
async def get_privacy_notice_endpoint(purpose: DataProcessingPurpose, user: dict = Depends(get_api_user)):
    """Get privacy notice for a processing purpose (GDPR Art. 12-14)."""
    notice = get_privacy_notice(purpose)
    lawful_basis = get_lawful_basis_description(LawfulBasis.CONTRACT)  # Default
    retention = get_data_retention_period(purpose.value.lower().replace("_", "_") + "_data")
    
    return {
        "purpose": purpose.value,
        "notice": notice,
        "lawful_basis": lawful_basis,
        "retention": retention,
        "rights": [
            "Access (Art. 15 GDPR)",
            "Rectification (Art. 16 GDPR)",
            "Erasure (Art. 17 GDPR)",
            "Restriction (Art. 18 GDPR)",
            "Portability (Art. 20 GDPR)",
            "Objection (Art. 21 GDPR)",
        ],
    }


@router.get("/dpia/{processing_activity}")
async def get_dpia_endpoint(processing_activity: str, user: dict = Depends(get_api_user)):
    """Get DPIA for a processing activity (GDPR Art. 35)."""
    if user.get("role") not in ("admin", "corporate_admin"):
        raise HTTPException(status_code=403, detail={"error": "admin_required"})
    
    dpia = get_dpia(processing_activity)
    if not dpia:
        required = check_dpia_required(processing_activity)
        return {
            "processing_activity": processing_activity,
            "dpia_required": required,
            "message": "DPIA not found" if required else "DPIA not required for this activity",
        }
    
    return dpia


@router.get("/compliance-status")
async def compliance_status(user: dict = Depends(get_dashboard_user)):
    """Get compliance status dashboard (admin only)."""
    return get_compliance_status()


@router.get("/records-of-processing")
async def get_ropa_endpoint(user: dict = Depends(get_dashboard_user)):
    """Get Records of Processing Activities (GDPR Art. 30, nFADP Art. 12)."""
    from app.compliance import ROPA_ENTRIES
    return {"processing_activities": ROPA_ENTRIES}


@router.get("/lawful-basis/{basis}")
async def get_lawful_basis(basis: LawfulBasis, user: dict = Depends(get_api_user)):
    """Get description of lawful basis."""
    return {
        "basis": basis.value,
        "description": get_lawful_basis_description(basis),
    }


@router.get("/retention/{category}")
async def get_retention(category: str, user: dict = Depends(get_api_user)):
    """Get data retention period for a category."""
    return {
        "category": category,
        "retention": get_data_retention_period(category),
    }


@router.post("/privacy-by-default")
async def apply_privacy_defaults(request: Request, user: dict = Depends(get_api_user)):
    """Apply Privacy by Default settings to user profile."""
    defaults = apply_privacy_by_default({})
    
    # Apply consent defaults (opt-out of non-essential)
    for consent_type, granted in defaults["consent_defaults"].items():
        try:
            ct = ConsentType(consent_type)
            await record_consent(user["_id"], ct, granted, request)
        except ValueError:
            pass
    
    return {"status": "privacy_defaults_applied", "defaults": defaults}


@router.post("/validate-minimization")
async def validate_minimization(request: Request, user: dict = Depends(get_dashboard_user)):
    """Validate data minimization for a processing purpose."""
    body = await request.json()
    purpose = DataProcessingPurpose(body.get("purpose"))
    data = body.get("data", {})
    
    warnings = validate_data_minimization(data, purpose)
    
    return {
        "purpose": purpose.value,
        "minimization_compliant": len(warnings) == 0,
        "unnecessary_fields": warnings,
    }


# --- Breach Notification ---

@router.post("/breach")
async def report_breach(request: Request, user: dict = Depends(get_dashboard_user)):
    """Report a data breach (admin only)."""
    if user.get("role") not in ("admin", "corporate_admin"):
        raise HTTPException(status_code=403, detail={"error": "admin_required"})
    
    body = await request.json()
    breach = await create_breach_record(
        title=body["title"],
        description=body["description"],
        severity=BreachSeverity(body["severity"]),
        data_categories=body["data_categories"],
        affected_users=body["affected_users"],
    )
    
    return {
        "breach_id": breach["_id"],
        "status": "recorded",
        "requires_72h_reporting": breach["severity"] in ["high", "critical"],
        "message": "Breach recorded. High/critical breaches must be reported to supervisory authority within 72 hours (GDPR Art. 33).",
    }


@router.get("/breach")
async def list_breaches(user: dict = Depends(get_dashboard_user)):
    """List breach records (admin only)."""
    breaches = await db["breach_records"].find({}).sort("discovered_at", -1).to_list(50)
    return {"breaches": breaches}