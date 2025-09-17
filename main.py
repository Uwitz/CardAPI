import os
import uvicorn
import random
import string
import binascii
import datetime

from urllib.parse import quote_plus
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
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

app.add_middleware(
	CORSMiddleware,
	allow_origins = ["https://portal.uwitz.cards"],
	allow_credentials = True,
	allow_methods = ["*"],
	allow_headers = ["*"]
)

@app.get("/")
async def read_root():
	return "OK"

@app.get("/{card_id}")
async def read_card(request: Request, card_id: str):
	try:
		data: dict = request.body()
		user_card = await collection.find_one({"_id": card_id})
		if not user_card:
			return RedirectResponse(url = "https://uwitz.cards")
		
		if user_card.get("status") == "pending" and not data:
			return RedirectResponse(url = f"https://portal.uwitz.cards/setup/{card_id}")

		if user_card.get("status") == "pending" and user_card.get("pin") == data.get("pin"):
			await collection.update_one(
				{"_id": card_id},
				{
					"$set": {
						"status": "active"
					}
				}
			)
			return JSONResponse(
				content = {
					"status": "active"
				}
			)

		elif user_card.get("status") == "pending" and user_card.get("pin") != data.get("pin"):
			return JSONResponse(
				content = {
					"error": "invalid_card_pin"
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

@app.get("/user/{user_id}")
async def head_user(request: Request, user_id: str):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user or not auth_user.get("is_admin") and not auth_user.get("_id") == user_id:
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)
	user_record = await db["users"].find_one({"_id": user_id})
	if not user_record:
		return JSONResponse(
			{
				"error": "not_found"
			},
			404
		)
	else:
		return JSONResponse(
			content = {
				"id": user_record.get("_id"),
				"display_name": user_record.get("display_name"),
				"name": user_record.get("name"),
				"plan_expiry": user_record.get("plan_expiry"),
				"referral": user_record.get("referral"),
				"referral_reward": user_record.get("referral_reward", 0.0),
				"currency": user_record.get("currency", "MYR"),
				"payouts": user_record.get("payouts", []),
				"is_admin": user_record.get("is_admin"),
				"username": user_record.get("username"),
				"plan": user_record.get("plan"),
				"organisation": user_record.get("organisation"),
				"status": user_record.get("status"),
				"transactions": user_record.get("transactions"),
				"created_at": user_record.get("created_at"),
				"updated_at": user_record.get("updated_at") if user_record.get("updated_at") else None
			},
			status_code = 200
		)

@app.get("/meta/{card_id}")
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
				"tier": user_card.get("tier"),
				"type": user_card.get("type"),
				"content": user_card.get("content"),
				"payment_id": user_card.get("payment_id"),
				"organisation": user_card.get("organisation"),
				"views": user_card.get("views", 0),
				"status": user_card.get("status"),
				"version": user_card.get("version"),
				"created_at": user_card.get("created_at"),
				"updated_at": user_card.get("updated_at")
			},
			status_code = 200
		)

@app.post("/profile")
async def user_profile(request: Request, data: dict):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	data_user = await db["users"].find_one({"username": data.get("username")})
	if not (data.get("username") and data_user and not data_user.get("_id") == auth_user.get("_id")) or not auth_user or not data_user:
		return JSONResponse(
			content = {
				"error": "invalid_token"
			},
			status_code = 401
		)

	if auth_user.get("status") != "active":
		return JSONResponse(
			content = {
				"error": "access_denied"
			},
			status_code = 403
		)

	cards = [
		{
			"id": str(card.get("_id")),
			"tier": card.get("tier"),
			"owner_id": str(card.get("owner_id")),
			"type": card.get("type"),
			"content": card.get("content"),
			"payment_id": card.get("payment_id"),
			"organisation": card.get("organisation"),
			"views": card.get("views", 0),
			"status": card.get("status"),
			"version": card.get("version"),
			"created_at": card.get("created_at"),
			"updated_at": card.get("updated_at")
		}
		async for card in collection.find({"owner_id": data_user.get("_id")})
	]

	return {
		"id": str(auth_user.get("_id")),
		"display_name": auth_user.get("display_name"),
		"name": auth_user.get("name"),
		"plan_expiry": auth_user.get("plan_expiry"),
		"referral": auth_user.get("referral"),
		"referral_reward": auth_user.get("referral_reward", 0.0),
		"currency": auth_user.get("currency", "MYR"),
		"payouts": auth_user.get("payouts", []),
		"username": auth_user.get("username"),
		"token": auth_user.get("token"),
		"is_admin": auth_user.get("is_admin"),
		"plan": auth_user.get("plan"),
		"organisation": auth_user.get("organisation"),
		"status": auth_user.get("status"),
		"transactions": auth_user.get("transactions"),
		"cards": cards,
		"created_at": auth_user.get("created_at"),
		"updated_at": auth_user.get("updated_at") if auth_user.get("updated_at") else None
	}

@app.get("/users")
async def list_users(request: Request):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user or not auth_user.get("is_admin"):
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)
	user_list = []
	try:
		async for user in db["users"].find({}):
				user_list.append(
					{
						"id": str(user.get("_id")),
						"display_name": user.get("display_name"),
						"name": user.get("name"),
						"plan_expiry": user.get("plan_expiry"),
						"referral": user.get("referral"),
						"referral_reward": user.get("referral_reward", 0.0),
						"currency": user.get("currency", "MYR"),
						"payouts": user.get("payouts", []),
						"username": user.get("username"),
						"is_admin": user.get("is_admin"),
						"plan": user.get("plan"),
						"organisation": user.get("organisation"),
						"status": user.get("status"),
						"transactions": user.get("transactions"),
						"created_at": user.get("created_at"),
						"updated_at": user.get("updated_at") if user.get("updated_at") else None
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
		print(f"Database error in list_users: {e}")
		return JSONResponse(
			content = {
				"error": "internal"
			},
			status_code = 500
		)
	return {
		"users": user_list
	}

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
		if auth_user.get("is_admin"):
			async for card in collection.find({}):
				user_cards.append(
					{
						"id": str(card.get("_id")),
						"tier": card.get("tier"),
						"owner_id": str(card.get("owner_id")),
						"type": card.get("type"),
						"content": card.get("content"),
						"payment_id": card.get("payment_id"),
						"organisation": card.get("organisation"),
						"views": card.get("views", 0),
						"status": card.get("status"),
						"version": card.get("version"),
						"created_at": card.get("created_at"),
						"updated_at": card.get("updated_at")
					}
				)
		else:
			async for card in collection.find({"owner_id": auth_user.get("_id")}):
				user_cards.append(
					{
						"id": str(card.get("_id")),
						"tier": card.get("tier"),
						"owner_id": str(card.get("owner_id")),
						"type": card.get("type"),
						"content": card.get("content"),
						"payment_id": card.get("payment_id"),
						"organisation": card.get("organisation"),
						"views": card.get("views", 0),
						"status": card.get("status"),
						"version": card.get("version"),
						"created_at": card.get("created_at"),
						"updated_at": card.get("updated_at")
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

@app.post("/payout")
async def create_payout_request(request: Request, payout: dict):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user:
		return JSONResponse({"error": "invalid_token"}, 401)
	if auth_user.get("plan_expiry") and int(auth_user.get("plan_expiry")) < int(datetime.datetime.now(datetime.timezone.utc).timestamp()):
		return JSONResponse({"error": "plan_expired"}, 403)
	code = "PAYOUT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k = 8))
	payout_entry = {
		"id": code,
		"amount": payout.get("amount", 0.0),
		"currency": payout.get("currency", auth_user.get("currency", "MYR")),
		"status": "pending",
		"created_at": str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
	}
	await db["users"].update_one({"_id": auth_user.get("_id")}, {"$push": {"payouts": payout_entry}})
	return {"payout_id": code, "status": "pending"}

@app.post("/admin/payout")
async def admin_mark_payout_claimed(request: Request, data: dict):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user or not auth_user.get("is_admin"):
		return JSONResponse({"error": "unauthorized"}, 401)
	user_id = data.get("user_id")
	payout_id = data.get("id")
	if not user_id or not payout_id:
		return JSONResponse({"error": "user_id_and_id_required"}, 400)
	ts = str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
	result = await db["users"].update_one(
		{"_id": user_id, "payouts.id": payout_id},
		{"$set": {"payouts.$.status": "claimed", "payouts.$.claimed_at": ts}}
	)
	if result.matched_count == 0:
		return JSONResponse({"error": "not_found"}, 404)
	return {"status": "claimed", "id": payout_id}

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

	username = str(user.get("username")).strip().lower()
	import re
	unix_username_pattern = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
	if not unix_username_pattern.match(username):
		return JSONResponse(
			content = {"error": "invalid_username"},
			status_code = 400
		)

	new_user = {
		"_id": "".join(random.choices(string.digits, k = 10)) + "." + str(int(datetime.datetime.now().timestamp())),
		"username": username,
		"name": user.get("name", None),
		"display_name": user.get("display_name", username),
		"plan_expiry": user.get("plan_expiry", None),
		"referral": user.get("referral", None),
		"referral_reward": user.get("referral_reward", 0.0),
		"currency": user.get("currency", "MYR"),
		"payouts": [],
		"token": binascii.hexlify(os.urandom(20)).decode(),
		"is_admin": False,
		"plan": user.get("plan", "individual"),
		"organisation": user.get("organisation", None),
		"status": "active",
		"transactions": [],
		"created_at": str(int(datetime.datetime.now(datetime.timezone.utc).timestamp())),
		"updated_at": str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
	}
	if await db["users"].find_one({"username": username, "_id": {"$ne": new_user["_id"]}}):
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
			"display_name": new_user["display_name"],
			"name": new_user["name"],
			"plan_expiry": new_user["plan_expiry"],
			"referral": new_user["referral"],
			"referral_reward": new_user["referral_reward"],
			"currency": new_user["currency"],
			"payouts": new_user["payouts"],
			"username": new_user["username"],
			"token": new_user["token"]
		},
		status_code = 201
	)

@app.post("/create/card")
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

	transaction = card.get("transaction")
	user_update_ops = {}
	if isinstance(transaction, dict):
		trans_entry_id = "".join(random.choices(string.ascii_uppercase + string.digits, k = 12))
		trans_entry = {
			"type": transaction.get("type"),
			"id": trans_entry_id,
			"bank": transaction.get("bank"),
			"gateway": transaction.get("gateway"),
			"reference": transaction.get("reference"),
			"amount": transaction.get("amount"),
			"timestamp": transaction.get("timestamp") or str(int(datetime.datetime.now(datetime.timezone.utc).timestamp())),
			"referral": transaction.get("referral")
		}
		user_update_ops["$push"] = {"transactions": trans_entry}

	payload = {
		"_id": "".join(random.choices(string.ascii_letters + string.digits, k = 8)),
		"tier": owner.get("plan", "individual"),
		"owner_id": card.get("owner_id"),
		"type": card.get("type"),
		"content": content,
		"payment_id": trans_entry_id,
		"organisation": owner.get("organisation", None),
		"views": 0,
		"status": "active" if not card.get("status") != "pending" else "pending",
		"version": 1.0,
		"created_at": str(int(datetime.datetime.now(datetime.timezone.utc).timestamp())),
		"updated_at": str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
	}
	result = await collection.insert_one(payload)
	if user_update_ops:
		await db["users"].update_one({"_id": card.get("owner_id")}, user_update_ops)
	return {"id": str(result.inserted_id)}

@app.patch("/{card_id}")
async def update_card(request: Request, card_id: str, card: dict):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	card_record = await collection.find_one(
		{
			"_id": card_id
		}
	)
	if not auth_user or not auth_user.get("is_admin"):
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)
	owner = await db["users"].find_one({"_id": card_record.get("owner_id")})
	if owner and owner.get("plan_expiry") and int(owner.get("plan_expiry")) < int(datetime.datetime.now(datetime.timezone.utc).timestamp()):
		return JSONResponse({"error": "plan_expired"}, 403)

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
		update_fields["$set"]["updated_at"] = str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
		await collection.update_one({"_id": card_id}, update_fields)
		return {"status": "success"}
	else:
		return JSONResponse(
			content = {
				"error": "no_fields_to_update"
			},
			status_code = 400
		)

@app.delete("/{card_id}")
async def delete_card(request: Request, card_id: str):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	card_record = await collection.find_one(
		{
			"_id": card_id
		}
	)
	if not auth_user or not auth_user.get("is_admin"):
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)
	await collection.delete_one({"_id": card_id})
	return {"status": "success"}

@app.delete("/{user_id}")
async def terminate_user(request: Request, user_id: str):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user:
		return JSONResponse(
			{
				"error": "invalid_token"
			},
			401
		)
	if not auth_user.get("is_admin"):
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)

	elif auth_user.get("_id") == user_id:
		await db["users"].delete_one({"_id": user_id})
		await collection.delete_many({"owner_id": user_id})
		return JSONResponse(
			{
				"status": "success"
			},
			200
		)

	else:
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)

@app.post("/renew/user/{user_id}")
async def admin_renew_user_plan(request: Request, user_id: str, data: dict):
	auth_user = await db["users"].find_one({"token": request.headers.get("Authorization")})
	if not auth_user or not auth_user.get("is_admin"):
		return JSONResponse(
			{
				"error": "unauthorized"
			},
			401
		)

	updates = {}
	if data.get("plan"):
		updates["plan"] = data.get("plan")
	if "plan_expiry" in data:
		updates["plan_expiry"] = data.get("plan_expiry")

	transaction_update = None
	transaction = data.get("transaction")
	if isinstance(transaction, dict):
		transaction_id = "".join(random.choices(string.ascii_uppercase + string.digits, k = 10))
		transaction_update = {
			"type": transaction.get("type"),
			"id": transaction_id,
			"bank": transaction.get("bank"),
			"gateway": transaction.get("gateway"),
			"reference": transaction.get("reference"),
			"amount": transaction.get("amount"),
			"timestamp": transaction.get("timestamp") or str(int(datetime.datetime.now(datetime.timezone.utc).timestamp())),
			"referral": transaction.get("referral")
		}

	if not updates and not transaction_update:
		return JSONResponse(
			{
				"error": "no_fields_to_update"
			},
			400
		)
	updates["updated_at"] = str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))

	update_ops = {"$set": updates}
	if transaction_update:
		update_ops["$push"] = {"transactions": transaction_update}

	result = await db["users"].update_one({"_id": user_id}, update_ops)
	if result.matched_count == 0:
		return JSONResponse({"error": "not_found"}, 404)
	user_record = await db["users"].find_one({"_id": user_id})
	return JSONResponse(
		content = {
			"id": user_record.get("_id"),
			"name": user_record.get("name"),
			"plan": user_record.get("plan"),
			"plan_expiry": user_record.get("plan_expiry"),
			"username": user_record.get("username"),
			"organisation": user_record.get("organisation"),
			"status": user_record.get("status"),
			"transactions": user_record.get("transactions"),
			"created_at": user_record.get("created_at"),
			"updated_at": user_record.get("updated_at")
		},
		status_code = 200
	)

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
				"tier": card.get("tier"),
				"owner_id": str(card.get("owner_id")),
				"type": card.get("type"),
				"content": card.get("content"),
				"payment_id": card.get("payment_id"),
				"organisation": card.get("organisation"),
				"views": card.get("views", 0),
				"status": card.get("status", "active"),
				"version": card.get("version"),
				"created_at": card.get("created_at"),
				"updated_at": card.get("updated_at")
			}
		)
	return {
		"user": {
			"id": str(auth_user.get("_id")),
			"display_name": auth_user.get("display_name"),
			"name": auth_user.get("name"),
			"plan_expiry": auth_user.get("plan_expiry"),
			"referral": auth_user.get("referral"),
			"referral_reward": auth_user.get("referral_reward", 0.0),
			"currency": auth_user.get("currency", "MYR"),
			"payouts": auth_user.get("payouts", []),
			"username": auth_user.get("username"),
			"token": auth_user.get("token"),
			"is_admin": auth_user.get("is_admin"),
			"plan": auth_user.get("plan"),
			"organisation": auth_user.get("organisation"),
			"status": auth_user.get("status"),
			"transactions": auth_user.get("transactions"),
			"created_at": auth_user.get("created_at"),
			"updated_at": auth_user.get("updated_at") if auth_user.get("updated_at") else None
		},
		"cards": user_cards
	}

if __name__ == "__main__":
	uvicorn.run(app, host = "127.0.0.1", port = 8000)