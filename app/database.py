from datetime import datetime, timezone
import os
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import get_settings

_settings = get_settings()

_connect_kwargs = {"serverSelectionTimeoutMS": 5000}
_tls_cert = os.environ.get("MONGO_TLS_CERT", "./certs/mongo.pem")
_tls_ca = os.environ.get("MONGO_TLS_CA", "./certs/ca.crt")
if os.path.exists(_tls_cert):
    _connect_kwargs["tls"] = True
    _connect_kwargs["tlsCertificateKeyFile"] = _tls_cert
    _connect_kwargs["tlsCAFile"] = _tls_ca
    _connect_kwargs["tlsAllowInvalidCertificates"] = True

db_client = AsyncIOMotorClient(_settings.MONGO_URI, **_connect_kwargs)
db = db_client[_settings.MONGO_DB]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes():
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

    await db["orders"].create_index("user_id")
    await db["orders"].create_index("org_id")
    await db["orders"].create_index("status")
    await db["orders"].create_index([("created_at", -1)])

    await db["subscriptions"].create_index("user_id")
    await db["subscriptions"].create_index("card_id")
    await db["subscriptions"].create_index("stripe_subscription_id")
    await db["subscriptions"].create_index("status")
    await db["subscriptions"].create_index("current_period_end")

    await db["email_logs"].create_index([("created_at", -1)])
