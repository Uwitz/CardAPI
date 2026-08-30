from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_api_user
from app.database import db, now_iso

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(user: dict) -> None:
    if not user.get("is_admin"):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})


@router.post("/payouts")
async def process_payout(request: Request, user: dict = Depends(get_api_user)):
    _require_admin(user)
    body = await request.json()
    user_id = body.get("user_id")
    payout_id = body.get("id")
    if not user_id or not payout_id:
        raise HTTPException(status_code=400, detail={"error": "user_id_and_id_required"})

    result = await db["users"].update_one(
        {"_id": user_id, "payouts.id": payout_id},
        {"$set": {"payouts.$.status": "claimed", "payouts.$.claimed_at": now_iso()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"status": "claimed", "id": payout_id}


@router.get("/orders")
async def list_all_orders(page: int = 1, limit: int = 50, user: dict = Depends(get_api_user)):
    _require_admin(user)
    skip = (page - 1) * limit
    orders = []
    async for o in db["orders"].find({}).skip(skip).limit(limit):
        orders.append({
            "id": o.get("_id"),
            "user_id": o.get("user_id"),
            "tier": o.get("tier"),
            "amount": o.get("amount"),
            "currency": o.get("currency"),
            "status": o.get("status"),
            "card_id": o.get("card_id"),
            "created_at": o.get("created_at"),
        })
    total = await db["orders"].count_documents({})
    return {"orders": orders, "page": page, "limit": limit, "total": total}


@router.patch("/orders/{order_id}")
async def update_order(order_id: str, request: Request, user: dict = Depends(get_api_user)):
    _require_admin(user)
    body = await request.json()
    allowed_statuses = {"pending", "paid", "shipped", "delivered", "cancelled", "refunded"}
    new_status = body.get("status")
    if new_status and new_status not in allowed_statuses:
        raise HTTPException(status_code=400, detail={"error": "invalid_status"})

    updates = {"updated_at": now_iso()}
    if new_status:
        updates["status"] = new_status
    result = await db["orders"].update_one({"_id": order_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"status": new_status}
