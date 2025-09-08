import os
import uvicorn
import random
import string
import binascii
import datetime

from urllib.parse import quote_plus
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ServerSelectionTimeoutError

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
	try:
		user_card = await collection.find_one({"_id": card_id})
		if not user_card:
			return RedirectResponse(url = "https://snyco.dev")
	except ServerSelectionTimeoutError:
		return JSONResponse(
			content = {
				"error": "timeout"
			},
			status_code = 503
		)
	except Exception as e:
		print(f"Database error in read_card: {e}")
		return JSONResponse(
			content = {
				"error": "internal"
			},
			status_code = 500
		)
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

@app.post("/create/user")
async def create_user(request: Request, user: dict):
	auth_user = await db["admin"].find_one({"token": request.headers.get("Authorization")})
	try:
		if auth_user is None:
			return JSONResponse(
				content = {
					"error": "unauthorized"
				},
				status_code = 401
			)
	except ServerSelectionTimeoutError:
		return JSONResponse(
			content = {
				"error": "timeout"
			},
			status_code = 503
		)
	except Exception as e:
		print(f"Database error in auth check: {e}")
		return JSONResponse(
			content = {
				"error": "internal"
			},
			status_code = 500
		)

	if not user.get("username"):
		return JSONResponse(
			content = {
				"error": "username_missing"
			},
			status_code = 400
		)

	new_user = {
		"_id": "".join(random.choices(string.digits, k = 10)) + "." + str(int(datetime.datetime.now().timestamp())),
		"username": user.get("username"),
		"token": binascii.hexlify(os.urandom(20)).decode(),
		"is_admin": False,
		"created_at": datetime.datetime.now(datetime.timezone.utc)
	}
	if await db["users"].find_one({"username": user.get("username"), "_id": {"$ne": new_user["_id"]}}):
		return JSONResponse(
			content = {
				"error": "duplicate_username"
			},
			status_code = 409
		)
	else:
		await db["users"].insert_one(new_user)
	return JSONResponse(
		content = {
			"id": str(new_user["_id"]),
			"username": new_user["username"],
			"token": new_user["token"]
		},
		status_code = 201
	)

@app.post("/create/link")
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
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user or not auth_user.get("is_admin"):
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)

	if card.get("type") not in ["vcard", "url"]:
		return JSONResponse(
			content = {
				"error": "invalid_type"
			},
			status_code = 400
		)

	elif card.get("type") == "vcard":
		vcard = card.get("vcard")
		if not vcard or not vcard.startswith("BEGIN:VCARD") or not vcard.endswith("END:VCARD"):
			return JSONResponse(
				content = {
					"error": "invalid_format"
				},
				status_code = 400
			)
		card["vcard"] = vcard
		owner = await db["users"].find_one({"_id": card.get("owner_id")})
		if not owner:
			return JSONResponse(
				content = {
					"error": "invalid_owner_id"
				},
				status_code = 400
			)

		payload = {
			"_id": "".join(random.choices(string.ascii_letters + string.digits, k = 8)),
			"owner_id": card.get("owner_id"),
			"payment_id": card.get("payment_id", None),
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
		owner = await db["users"].find_one({"_id": card.get("owner_id")})
		if not owner:
			return JSONResponse(
				content = {
					"error": "invalid_owner_id"
				},
				status_code = 400
			)

		payload = {
			"_id": "".join(random.choices(string.ascii_letters + string.digits, k = 8)),
			"owner": card.get("owner_id"),
			"payment_id": card.get("payment_id", None),
			"type": "url",
			"url": url,
			"created_at": datetime.datetime.now(datetime.timezone.utc),
			"updated_at": datetime.datetime.now(datetime.timezone.utc),
			"views": 0
		}
		if not url or not (url.startswith("http://") or url.startswith("https://")):
			return JSONResponse(
				content = {
					"error": "invalid_url"
				},
				status_code = 400
			)
		result = await collection.insert_one(payload)
		return {"id": str(result.inserted_id)}

if __name__ == "__main__":
	uvicorn.run(app, host = "127.0.0.1", port = 8000)