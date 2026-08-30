import os
from datetime import datetime, timezone
from urllib.parse import quote_plus

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import get_settings

_settings = get_settings()

db_client = AsyncIOMotorClient(
    f"mongodb://{quote_plus(_settings.MONGO_USER)}:{quote_plus(_settings.MONGO_PASS)}@{quote_plus(_settings.MONGO_HOST)}",
    tls=True,
    tlsCAFile=os.environ.get("MONGO_CA_FILE", "./certs/ca.crt"),
    minPoolSize=2,
    maxPoolSize=10,
    serverSelectionTimeoutMS=5000,
)
db = db_client[_settings.MONGO_DB]


def now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
