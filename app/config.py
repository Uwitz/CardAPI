from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MongoDB
    MONGO_USER: str
    MONGO_PASS: str
    MONGO_HOST: str
    MONGO_DB: str = "cards"

    # Security
    SESSION_SECRET: str
    ALLOWED_ORIGIN: str = "https://portal.uwitz.cards"

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_DIGITAL: str = ""
    STRIPE_PRICE_PLASTIC: str = ""
    STRIPE_PRICE_PREMIUM: str = ""

    # Site
    SITE_URL: str = "https://portal.uwitz.cards"
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
