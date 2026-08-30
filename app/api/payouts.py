import random
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_api_user
from app.database import db, now_iso

router = APIRouter(prefix="/api/payouts", tags=["payouts"])


@router.post("")
async def request_payout(request: Request, user: dict = Depends(get_api_user)):
    body = await request.json()
    amount = body.get("amount", 0.0)
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise HTTPException(status_code=400, detail={"error": "invalid_amount"})

    # Plan expiry check
    if user.get("plan_expiry") and int(user["plan_expiry"]) < int(datetime.now(timezone.utc).timestamp()):
        raise HTTPException(status_code=403, detail={"error": "plan_expired"})

    code = "PAYOUT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    entry = {
        "id": code,
        "amount": float(amount),
        "currency": body.get("currency", user.get("currency", "MYR")),
        "status": "pending",
        "created_at": now_iso(),
    }
    await db["users"].update_one({"_id": user.get("_id")}, {"$push": {"payouts": entry}})
    return {"payout_id": code, "status": "pending"}
