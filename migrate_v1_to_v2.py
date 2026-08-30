#!/usr/bin/env python3
"""Migrate existing CardAPI MongoDB data to v2 schema.

Run: python3 migrate_v1_to_v2.py

This script:
- Converts string Unix timestamps to ISO 8601
- Moves db["admin"] tokens into db["users"] with is_admin=True
- Ensures all users have is_admin field
- Adds MongoDB indexes
- Is idempotent (safe to run multiple times)
"""
import asyncio
import os
import re
from datetime import datetime, timezone

from dotenv import find_dotenv, load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from urllib.parse import quote_plus

load_dotenv(find_dotenv())


async def migrate():
    client = AsyncIOMotorClient(
        f"mongodb://{quote_plus(os.getenv('MONGO_USER'))}:{quote_plus(os.getenv('MONGO_PASS'))}@{quote_plus(os.getenv('MONGO_HOST'))}",
        tls=True,
        tlsCAFile=os.environ.get("MONGO_CA_FILE", "./certs/ca.crt"),
    )
    db = client[os.getenv("MONGO_DB", "cards")]

    timestamp_re = re.compile(r"^\d{10,13}$")
    summary = {"cards": 0, "users": 0, "admins_migrated": 0, "indexes_created": 0}

    # --- Migrate user_cards ---
    async for card in db["user_cards"].find({}):
        updates = {}
        for field in ("created_at", "updated_at"):
            v = card.get(field)
            if isinstance(v, str) and timestamp_re.match(v):
                ts = int(v)
                if ts > 1e12:  # milliseconds
                    ts = ts / 1000
                updates[field] = datetime.fromtimestamp(ts, timezone.utc).isoformat()
        if updates:
            await db["user_cards"].update_one({"_id": card["_id"]}, {"$set": updates})
            summary["cards"] += 1

    # --- Migrate users: ensure is_admin, lowercase username, convert timestamps ---
    seen_usernames: dict[str, str] = {}  # lowercased -> _id of first user to use it
    async for user in db["users"].find({}):
        updates = {}
        if "is_admin" not in user:
            updates["is_admin"] = False
        # Lowercase username to prevent case-sensitivity bypass
        uname = user.get("username")
        if uname and uname != uname.lower():
            # Check for collision before lowercasing
            lower_uname = uname.lower()
            if lower_uname in seen_usernames and seen_usernames[lower_uname] != user["_id"]:
                print(f"WARNING: duplicate username after lowercasing: {uname} vs {lower_uname} (ids: {user['_id']}, {seen_usernames[lower_uname]})")
            else:
                updates["username"] = lower_uname
                seen_usernames[lower_uname] = user["_id"]
        for field in ("created_at", "updated_at", "plan_expiry"):
            v = user.get(field)
            if isinstance(v, str) and timestamp_re.match(v):
                ts = int(v)
                if ts > 1e12:
                    ts = ts / 1000
                updates[field] = datetime.fromtimestamp(ts, timezone.utc).isoformat()
        if updates:
            await db["users"].update_one({"_id": user["_id"]}, {"$set": updates})
            summary["users"] += 1

    # --- Move db["admin"] into db["users"] with is_admin=True ---
    try:
        async for admin in db["admin"].find({}):
            token = admin.get("token")
            if not token:
                continue
            # If a user with this token exists, just set is_admin=True
            result = await db["users"].update_one(
                {"token": token},
                {"$set": {"is_admin": True, "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            if result.matched_count == 0:
                # No matching user — create one
                new_user = {
                    "_id": admin.get("_id") or admin.get("username", "admin-" + token[:8]),
                    "username": admin.get("username", "admin"),
                    "display_name": admin.get("display_name", "Administrator"),
                    "email": admin.get("email"),
                    "token": token,
                    "is_admin": True,
                    "plan": "enterprise",
                    "status": "active",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "payouts": [],
                    "transactions": [],
                    "referral_reward": 0,
                    "currency": "MYR",
                }
                await db["users"].insert_one(new_user)
            summary["admins_migrated"] += 1
        # Drop the old admin collection
        await db["admin"].drop()
    except Exception as e:
        print(f"Note: no admin collection to migrate: {e}")

    # --- Create indexes ---
    index_specs = [
        (db["user_cards"], [("owner_id", 1)]),
        (db["user_cards"], [("status", 1)]),
        (db["user_cards"], [("created_at", -1)]),
        (db["users"], [("token", 1)]),
        (db["users"], [("username", 1)], {"unique": True}),
        (db["orders"], [("user_id", 1)]),
        (db["orders"], [("status", 1)]),
        (db["orders"], [("created_at", -1)]),
        (db["webhook_events"], [("processed_at", -1)]),
        (db["verified_domains"], [("user_id", 1)]),
        (db["verified_domains"], [("domain", 1), ("user_id", 1)]),
        (db["verified_domains"], [("domain", 1), ("verified", 1)]),
    ]
    for spec in index_specs:
        coll = spec[0]
        keys = spec[1]
        kwargs = spec[2] if len(spec) > 2 else {}
        try:
            await coll.create_index(keys, **kwargs)
            summary["indexes_created"] += 1
        except Exception as e:
            print(f"Index note {keys}: {e}")

    print("Migration complete:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(migrate())
