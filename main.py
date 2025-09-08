import os
import uvicorn
import random
import string
import uuid
import datetime

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from urllib.parse import quote_plus
from dotenv import find_dotenv, load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(find_dotenv())
app = FastAPI()
db = AsyncIOMotorClient(
	f"mongodb://{quote_plus(os.getenv("MONGO_USER"))}:{quote_plus(os.getenv("MONGO_PASS"))}@10.0.0.2:27017",
	tls = True,
	tlsCertificateKeyFile = "./certs/mongo.pem",
	tlsCAFile = "./certs/ca.crt",
	tlsAllowInvalidCertificates = True
)["cards"]
collection = db["user_cards"]

@app.get("/")
async def read_root():
	return "OK"

@app.get("/{card_id}")
async def read_card(card_id: str):
	user_card = await collection.find_one({"_id": card_id})
	if not user_card:
		return RedirectResponse(url = "https://snyco.dev")
	if user_card.get("type") == "vcard":
		return Response(
			content = user_card.get("vcard"),
			media_type = "text/vcard",
			headers = {
				"Content-Disposition": "attachment; filename=contact.vcf"
			}
		)
	elif user_card.get("type") == "url":
		return RedirectResponse(url = user_card.get("url"))
	else:
		return RedirectResponse(url = "https://snyco.dev")

@app.post("/create")
async def create_card(request: Request, card: dict):
	"""
		card: {
			"type": "vcard" | "url",
			"vcard": "...", (if type is vcard)
			"url": "...", (if type is url)
			"owner_id": "optional, if not provided a new user will be created",
			"username": "optional, for new user creation",
			"payment_id": "optional, for tracking payments"
		}
	"""
	auth_header = request.get("Authorization")
	if await db.admin.find_one({"token": auth_header}) is None:
		return {"error": "Unauthorized"}, 401

	if card.get("type") not in ["vcard", "url"]:
		return {"error": "Invalid card type"}, 400

	elif card.get("type") == "vcard":
		vcard = card.get("vcard")
		if not vcard or not vcard.startswith("BEGIN:VCARD") or not vcard.endswith("END:VCARD"):
			return {"error": "Invalid vCard format"}, 400
		card["vcard"] = vcard
		if not await db.users.find_one({"_id": card.get("owner_id")}):
			card["owner_id"] = uuid.uuid4()
			await db.users.insert_one(
				{
					"_id": card["owner_id"],
					"username": card.get("username", "unknown"),
					"token": "".join(random.choices(string.ascii_letters + string.digits, k = 16))
				}
			)
		payload = {
			"_id": "".join(random.choices(string.ascii_letters + string.digits, k = 8)),
			"owner": {
				"id": card.get("owner_id"),
				"payment_id": card.get("payment_id", None)
			},
			"type": "vcard",
			"vcard": vcard,
			"created_at": datetime.datetime.now(datetime.timezone.utc),
			"updated_at": datetime.datetime.now(datetime.timezone.utc),
			"views": 0
		}
		result = await collection.insert_one(payload)
		return {"id": str(result.inserted_id)}

	elif card.get("type") == "url":
		url = card.get("url")
		if not await db.users.find_one({"_id": card.get("owner_id")}):
			card["owner_id"] = uuid.uuid4()
			await db.users.insert_one(
				{
					"_id": card["owner_id"],
					"username": card.get("username", "unknown"),
					"token": "".join(random.choices(string.ascii_letters + string.digits, k = 16))
				}
			)
		payload = {
			"_id": "".join(random.choices(string.ascii_letters + string.digits, k = 8)),
			"owner": {
				"id": card.get("owner_id"),
				"payment_id": card.get("payment_id", None)
			},
			"type": "url",
			"url": url,
			"created_at": datetime.datetime.now(datetime.timezone.utc),
			"updated_at": datetime.datetime.now(datetime.timezone.utc),
			"views": 0
		}
		if not url or not (url.startswith("http://") or url.startswith("https://")):
			return {"error": "Invalid URL"}, 400
		result = await collection.insert_one(payload)
		return {"id": str(result.inserted_id)}

if __name__ == "__main__":
	uvicorn.run(app, host = "127.0.0.1", port = 8000)