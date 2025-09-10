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
	f"mongodb://{quote_plus(os.getenv('MONGO_USER'))}:{quote_plus(os.getenv('MONGO_PASS'))}@{quote_plus(os.getenv('MONGO_HOST'))}",
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
			return RedirectResponse(url = "https://uwitz.cards")
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
			content = user_card.get("content"),
			media_type = "text/vcard",
			headers = {
				"Content-Disposition": "attachment; filename=contact.vcf"
			}
		)
	elif user_card.get("type") == "url":
		return RedirectResponse(url = user_card.get("url"))
	else:
		return RedirectResponse(url = "https://uwitz.cards")

@app.head("/{card_id}")
async def head_card(request: Request, card_id: str):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	user_card = await collection.find_one({"_id": card_id})
	if not auth_user:
		return JSONResponse(
			content = {
				"error": "token_required"
			},
			status_code = 400
		)
	if (not user_card or not auth_user.get("_id") == user_card.get("owner_id")) and not auth_user.get("is_admin"):
		return JSONResponse(
			content = {
				"error": "not_found"
			},
			status_code = 404
		)

	else:
		return JSONResponse(
			content = {
				"type": user_card.get("type"),
				"content": user_card.get("content"),
				"payment_id": user_card.get("payment_id"),
				"created_at": user_card.get("created_at"),
				"updated_at": user_card.get("updated_at"),
				"views": user_card.get("views", 0)
			},
			status_code = 200
		)

@app.get("/cards")
async def list_cards(request: Request):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user or not auth_user.get("is_admin"):
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)
	user_cards = []
	try:
		async for card in collection.find({}):
				user_cards.append(
					{
						"id": str(card.get("_id")),
						"payment_id": card.get("payment_id"),
						"type": card.get("type"),
						"content": card.get("content"),
						"created_at": card.get("created_at"),
						"updated_at": card.get("updated_at"),
						"views": card.get("views", 0)
					}
				)
	except ServerSelectionTimeoutError:
		return JSONResponse(
			content = {
				"error": "timeout"
			},
			status_code = 503
		)
	except Exception as e:
		print(f"Database error in list_cards: {e}")
		return JSONResponse(
			content = {
				"error": "internal"
			},
			status_code = 500
		)
	return {
		"cards": user_cards
	}

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
			"content": "...",
			"owner_id": "optional, if not provided a new user will be created",
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

	content = card.get("content")
	if card.get("type") == "vcard" and (
		not content or not (content.startswith("BEGIN:VCARD") or content.endswith("END:VCARD"))
	):
		return JSONResponse(
			content = {
				"error": "invalid_format"
			},
			status_code = 400
		)

	elif card.get("type") == "url" and (
		not content or not (content.startswith("http://") or content.startswith("https://"))
	):
		return JSONResponse(
			content = {
				"error": "invalid_url"
			},
			status_code = 400
		)

	content = card.get("content")
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
		"type": card.get("type"),
		"content": content,
		"created_at": datetime.datetime.now(datetime.timezone.utc),
		"updated_at": datetime.datetime.now(datetime.timezone.utc),
		"views": 0
	}
	result = await collection.insert_one(payload)
	return {"id": str(result.inserted_id)}

@app.patch("/{card_id}")
async def update_card(request: Request, card_id: str, card: dict):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	card_record = await collection.find_one(
		{
			"_id": card_id
		}
	)
	if not auth_user or not auth_user.get("token") == card_record.get("owner_id"):
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)

	update_fields = {}
	if card.get("type") == "vcard":
		content = card.get("content")
		if not content or not content.startswith("BEGIN:VCARD") or not content.endswith("END:VCARD"):
			return JSONResponse(
				content = {
					"error": "invalid_format"
				},
				status_code = 400
			)
		update_fields["$set"] = {"content": content}

	elif card.get("type") == "url":
		content = card.get("content")
		if not content or not (content.startswith("http://") or content.startswith("https://")):
			return JSONResponse(
				content = {
					"error": "invalid_url"
				},
				status_code = 400
			)
		update_fields["$set"] = {"content": content}

	else:
		return JSONResponse(
			content = {
				"error": "invalid_type"
			},
			status_code = 400
		)

	if not update_fields == {}:
		update_fields["updated_at"] = datetime.datetime.now(datetime.timezone.utc)
		await collection.update_one({"_id": card_id}, update_fields)
		return {"status": "success"}
	else:
		return JSONResponse(
			content = {
				"error": "no_fields_to_update"
			},
			status_code = 400
		)

@app.delete("/delete/{card_id}")
async def delete_card(request: Request, card_id: str):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	card_record = await collection.find_one(
		{
			"_id": card_id
		}
	)
	if not auth_user or not auth_user.get("token") == card_record.get("owner_id") or not auth_user.get("is_admin"):
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)
	await collection.delete_one({"_id": card_id})
	return {"status": "success"}

@app.delete("/terminate")
async def terminate_user(request: Request):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user:
		return JSONResponse(
			{
				"error": "invalid_token"
			},
			401
		)
	await db["users"].delete_one({"_id": auth_user.get("_id")})
	await collection.delete_many({"owner_id": auth_user.get("_id")})
	return {"status": "success"}

@app.post("/request")
async def data_request(request: Request):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user:
		return JSONResponse(
			{
				"error": "invalid_token"
			},
			401
		)
	user_cards = []
	async for card in collection.find({"owner_id": auth_user.get("_id")}):
		user_cards.append(
			{
				"id": str(card.get("_id")),
				"owner_id": str(card.get("owner_id")),
				"payment_id": card.get("payment_id"),
				"type": card.get("type"),
				"content": card.get("content"),
				"created_at": card.get("created_at"),
				"updated_at": card.get("updated_at"),
				"views": card.get("views", 0)
			}
		)
	return {
		"user": {
			"id": str(auth_user.get("_id")),
			"username": auth_user.get("username"),
			"token": auth_user.get("token"),
			"is_admin": auth_user.get("is_admin"),
			"created_at": auth_user.get("created_at"),
			"updated_at": auth_user.get("updated_at") if auth_user.get("updated_at") else None
		},
		"cards": user_cards
	}

if __name__ == "__main__":
	uvicorn.run(app, host = "127.0.0.1", port = 8000)