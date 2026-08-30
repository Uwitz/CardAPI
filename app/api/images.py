import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.auth import get_api_user
from app.database import db, now_iso
from app.image_utils import validate_card_image

router = APIRouter(prefix="/api/cards", tags=["images"])

UPLOAD_DIR = "static/uploads/cards"


@router.post("/{card_id}/image")
async def upload_image(card_id: str, file: UploadFile, user: dict = Depends(get_api_user)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail={"error": "file_too_large"})

    valid, msg, info = validate_card_image(content)
    if not valid:
        raise HTTPException(status=400, detail={"error": msg})

    ext = os.path.splitext(file.filename or "upload.png")[1] or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(content)

    # Delete old image
    old = await db["card_images"].find_one({"card_id": card["_id"]})
    if old:
        old_path = os.path.join(UPLOAD_DIR, old["filename"])
        if os.path.exists(old_path):
            os.remove(old_path)
        await db["card_images"].delete_one({"_id": old["_id"]})

    now = now_iso()
    image_doc = {
        "_id": uuid.uuid4().hex,
        "card_id": card["_id"],
        "user_id": user["_id"],
        "filename": filename,
        "original_name": file.filename or filename,
        "file_size": len(content),
        "width_px": info["width"],
        "height_px": info["height"],
        "dpi": info["dpi"],
        "aspect_ratio": round(info["width"] / info["height"], 4),
        "is_approved": True,
        "created_at": now,
    }
    await db["card_images"].insert_one(image_doc)
    await db["cards"].update_one({"_id": card["_id"]}, {"$set": {"image_url": f"/static/uploads/cards/{filename}"}})

    return {"filename": filename, "url": f"/static/uploads/cards/{filename}", "width": info["width"], "height": info["height"]}


@router.get("/{card_id}/image")
async def get_image(card_id: str, user: dict = Depends(get_api_user)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    image = await db["card_images"].find_one({"card_id": card["_id"]})
    if not image:
        raise HTTPException(status_code=404, detail={"error": "no_image"})
    return image


@router.delete("/{card_id}/image")
async def delete_image(card_id: str, user: dict = Depends(get_api_user)):
    card = await db["cards"].find_one({"card_id": card_id})
    if not card or card["owner_id"] != user["_id"]:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    image = await db["card_images"].find_one({"card_id": card["_id"]})
    if not image:
        raise HTTPException(status_code=404, detail={"error": "no_image"})

    filepath = os.path.join(UPLOAD_DIR, image["filename"])
    if os.path.exists(filepath):
        os.remove(filepath)

    await db["card_images"].delete_one({"_id": image["_id"]})
    await db["cards"].update_one({"_id": card["_id"]}, {"$set": {"image_url": None}})
    return {"status": "deleted"}
