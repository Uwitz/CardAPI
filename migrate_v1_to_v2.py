#!/usr/bin/env python3
"""Migrate v2 (old) schema to v3 (rebuild) schema.

Run: python3 migrate_v1_to_v2.py

Field mapping:
  users:
    is_admin (bool)         → role: "admin" | "individual"
    plan                    → removed (use subscriptions collection)
    plan_expiry             → removed (use subscriptions.current_period_end)
    organisation            → org_id (lookup from organisations collection)
    token                   → kept as-is for API backward compat
    + new: password_hash, display_name, stripe_customer_id, referral_code

  user_cards → cards:
    tier ("plastic")        → card_tier ("physical")
    tier ("premium")        → card_tier ("physical")
    tier ("digital")        → card_tier ("digital")
    type ("vcard")          → card_type ("social")
    type ("url")            → card_type ("taglink")
    content (vcard text)    → vcard_data
    content (url)           → redirect_url
    + new: card_id (6-char short URL), subscription_id, subscription_status,
           frozen, freeze_reason, image_url, nfc_chip_id, serial_number, org_id

  orders:
    tier                    → card_tier (mapped same as cards)
    card_type ("vcard")     → card_type ("social"|"corporate")
    card_type ("url")       → card_type ("taglink")
    amount                  → amount_subtotal
    + new: amount_fees, amount_total, shipping_address

  verified_domains → kept as-is (not changing)
  webhook_events → stripe_events (rename)
"""
import asyncio
import os
import random
import re
import string
import secrets
from datetime import datetime, timezone

from dotenv import find_dotenv, load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(find_dotenv())

TIER_MAP = {"digital": "digital", "plastic": "physical", "premium": "physical"}
TYPE_MAP = {"vcard": "social", "url": "taglink"}
ROLE_MAP = {True: "admin", False: "individual"}
ALPHABET = string.ascii_letters + string.digits


def gen_short_id(length: int = 6) -> str:
    return "".join(random.choices(ALPHABET, k=length))


def gen_id(length: int = 10) -> str:
    return "".join(random.choices(string.digits, k=length))


def to_iso(v) -> str | None:
    if not v:
        return None
    if isinstance(v, str) and re.match(r"^\d{10,13}$", v):
        ts = int(v)
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    if isinstance(v, str) and "T" in v:
        return v
    return None


async def migrate():
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    tls_cert = os.environ.get("MONGO_TLS_CERT", "./certs/mongo.pem")
    tls_ca = os.environ.get("MONGO_TLS_CA", "./certs/ca.crt")

    connect_kwargs = {"serverSelectionTimeoutMS": 10000}
    if os.path.exists(tls_cert):
        connect_kwargs["tls"] = True
        connect_kwargs["tlsCertificateKeyFile"] = tls_cert
        connect_kwargs["tlsCAFile"] = tls_ca
        connect_kwargs["tlsAllowInvalidCertificates"] = True

    client = AsyncIOMotorClient(mongo_uri, **connect_kwargs)
    db = client[os.getenv("MONGO_DB", "cards")]

    s = {"users": 0, "cards": 0, "orders": 0, "renames": 0, "skipped": 0}

    # --- 1. Create organisations from old "organisation" strings ---
    org_map: dict[str, str] = {}  # org name → org _id
    org_users: dict[str, str] = {}  # org name → owner user _id

    async for user in db["users"].find({"organisation": {"$ne": None}}):
        org_name = user.get("organisation", "").strip()
        if not org_name:
            continue
        if org_name not in org_map:
            org_id = gen_id(8)
            slug = re.sub(r"[^a-z0-9]+", "-", org_name.lower()).strip("-")
            org_doc = {
                "_id": org_id,
                "name": org_name,
                "slug": slug,
                "owner_id": user["_id"],
                "billing_email": user.get("email", ""),
                "stripe_customer_id": None,
                "logo_url": None,
                "status": "active",
                "created_at": user.get("created_at", datetime.now(timezone.utc).isoformat()),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                await db["organisations"].insert_one(org_doc)
            except Exception:
                org_id = (await db["organisations"].find_one({"slug": slug})).get("_id", org_id)
            org_map[org_name] = org_id
            org_users[org_name] = user["_id"]
        else:
            org_users[org_name] = user["_id"]

    # --- 2. Migrate users ---
    async for user in db["users"].find({}):
        updates = {}

        # Role from is_admin
        is_admin = user.get("is_admin", False)
        updates["role"] = ROLE_MAP.get(is_admin, "individual")

        # display_name fallback
        if "display_name" not in user:
            updates["display_name"] = user.get("display_name", user.get("username", "User"))

        # org_id from organisation name
        org_name = user.get("organisation")
        if org_name and org_name in org_map:
            updates["org_id"] = org_map[org_name]
            if user.get("role") != "admin":
                updates["role"] = "corporate_admin" if org_users.get(org_name) == user["_id"] else "individual"

        # New fields
        if "stripe_customer_id" not in user:
            updates["stripe_customer_id"] = None
        if "referral_code" not in user:
            updates["referral_code"] = secrets.token_hex(3).upper()

        # Timestamps
        for field in ("created_at", "updated_at"):
            converted = to_iso(user.get(field))
            if converted and converted != user.get(field):
                updates[field] = converted

        if updates:
            await db["users"].update_one({"_id": user["_id"]}, {"$set": updates})
            s["users"] += 1

    # --- 3. Migrate user_cards → cards ---
    # Build order lookup: user_id → list of paid order card_ids
    paid_orders = {}
    async for order in db["orders"].find({"status": "paid"}):
        uid = order.get("user_id")
        if uid:
            paid_orders.setdefault(uid, []).append(order)

    used_card_ids = set()
    async for card in db["user_cards"].find({}):
        old_type = card.get("type", "vcard")
        old_tier = card.get("tier", "plastic")
        content = card.get("content", "")
        new_card_type = TYPE_MAP.get(old_type, "social")
        new_card_tier = TIER_MAP.get(old_tier, "physical")

        # Generate short card_id, ensure uniqueness
        while True:
            cid = gen_short_id(6)
            if cid not in used_card_ids:
                used_card_ids.add(cid)
                break

        # Determine subscription_status
        sub_status = "none"

        new_card = {
            "_id": card["_id"],  # keep old internal ID
            "card_id": cid,
            "owner_id": card.get("owner_id", ""),
            "org_id": None,
            "card_type": new_card_type,
            "card_tier": new_card_tier,
            "vcard_data": content if old_type == "vcard" else None,
            "redirect_url": content if old_type == "url" else None,
            "plain_text": None,
            "nfc_chip_id": None,
            "serial_number": None,
            "image_url": None,
            "subscription_id": None,
            "subscription_status": sub_status,
            "frozen": False,
            "freeze_reason": None,
            "views": card.get("views", 0),
            "status": card.get("status", "active"),
            "pin": card.get("pin"),
            "created_at": to_iso(card.get("created_at")) or datetime.now(timezone.utc).isoformat(),
            "updated_at": to_iso(card.get("updated_at")) or datetime.now(timezone.utc).isoformat(),
        }

        # Try to assign org_id from owner
        owner = await db["users"].find_one({"_id": new_card["owner_id"]})
        if owner and owner.get("org_id"):
            new_card["org_id"] = owner["org_id"]

        # Determine subscription from paid orders for this card
        owner_orders = paid_orders.get(new_card["owner_id"], [])
        for o in owner_orders:
            if o.get("card_type") == old_type or o.get("card_content", "") == content:
                new_card["subscription_status"] = "active"
                break

        try:
            await db["cards"].insert_one(new_card)
            s["cards"] += 1
        except Exception:
            s["skipped"] += 1

    # --- 4. Migrate orders ---
    async for order in db["orders"].find({}):
        updates = {}

        old_tier = order.get("tier", "digital")
        old_card_type = order.get("card_type", "vcard")
        updates["card_tier"] = TIER_MAP.get(old_tier, "digital")
        updates["card_type"] = TYPE_MAP.get(old_card_type, "social")

        # Price fields
        old_amount = order.get("amount", 0)
        if "amount_subtotal" not in order:
            updates["amount_subtotal"] = old_amount
            updates["amount_fees"] = 0
            updates["amount_total"] = old_amount

        if "shipping_address" not in order:
            updates["shipping_address"] = None

        # Timestamps
        for field in ("created_at", "updated_at"):
            converted = to_iso(order.get(field))
            if converted and converted != order.get(field):
                updates[field] = converted

        if updates:
            await db["orders"].update_one({"_id": order["_id"]}, {"$set": updates})
            s["orders"] += 1

    # --- 5. Rename webhook_events → stripe_events ---
    collections = await db.list_collection_names()
    if "webhook_events" in collections and "stripe_events" not in collections:
        await db["webhook_events"].rename("stripe_events")
        s["renames"] += 1
    elif "webhook_events" in collections:
        await db["webhook_events"].drop()
        s["renames"] += 1

    # --- 6. Create new indexes ---
    await db["users"].create_index("username", unique=True)
    await db["users"].create_index("email", unique=True)
    await db["users"].create_index("org_id")
    await db["users"].create_index("role")

    await db["organisations"].create_index("slug", unique=True)
    await db["organisations"].create_index("owner_id")

    await db["cards"].create_index("card_id", unique=True)
    await db["cards"].create_index("owner_id")
    await db["cards"].create_index("org_id")
    await db["cards"].create_index("card_type")
    await db["cards"].create_index("status")
    await db["cards"].create_index("subscription_status")

    await db["card_images"].create_index("card_id")
    await db["card_images"].create_index("user_id")

    await db["taglink_api_keys"].create_index("card_id")
    await db["taglink_api_keys"].create_index("key_hash", unique=True)

    await db["subscriptions"].create_index("user_id")
    await db["subscriptions"].create_index("card_id")
    await db["subscriptions"].create_index("stripe_subscription_id")
    await db["subscriptions"].create_index("status")
    await db["subscriptions"].create_index("current_period_end")

    await db["email_logs"].create_index([("created_at", -1)])

    print("Migration complete:")
    for k, v in s.items():
        print(f"  {k}: {v}")
    print(f"\nOld collections preserved (user_cards, webhook_events) — safe to drop after verification.")


if __name__ == "__main__":
    asyncio.run(migrate())
