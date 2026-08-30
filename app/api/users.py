from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_api_user
from app.database import db

router = APIRouter(prefix="/api/users", tags=["users"])


def _user_to_response(user: dict) -> dict:
    return {
        "id": user.get("_id"),
        "display_name": user.get("display_name"),
        "email": user.get("email"),
        "plan_expiry": user.get("plan_expiry"),
        "referral": user.get("referral"),
        "referral_reward": user.get("referral_reward", 0.0),
        "currency": user.get("currency", "MYR"),
        "payouts": user.get("payouts", []),
        "is_admin": user.get("is_admin", False),
        "username": user.get("username"),
        "plan": user.get("plan"),
        "organisation": user.get("organisation"),
        "status": user.get("status"),
        "transactions": user.get("transactions", []),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        # NOTE: token intentionally omitted
    }


def _card_to_response(card: dict) -> dict:
    return {
        "id": card.get("_id"),
        "tier": card.get("tier"),
        "owner_id": card.get("owner_id"),
        "type": card.get("type"),
        "content": card.get("content"),
        "payment_id": card.get("payment_id"),
        "organisation": card.get("organisation"),
        "views": card.get("views", 0),
        "status": card.get("status"),
        "version": card.get("version"),
        "created_at": card.get("created_at"),
        "updated_at": card.get("updated_at"),
    }


@router.get("/me")
async def get_me(user: dict = Depends(get_api_user)):
    """Get the authenticated user's full profile + their cards. (Consolidates old /profile + /request)"""
    cards = [_card_to_response(c) async for c in db["user_cards"].find({"owner_id": user.get("_id")})]
    return {**_user_to_response(user), "cards": cards}


@router.get("/{user_id}")
async def get_user(user_id: str, user: dict = Depends(get_api_user)):
    """Get a specific user. Admin or self only."""
    if user.get("_id") != user_id and not user.get("is_admin"):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    target = await db["users"].find_one({"_id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return _user_to_response(target)
