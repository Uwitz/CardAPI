from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MongoDB
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB: str = "cards"

    # Security
    SESSION_SECRET: str = ""
    ALLOWED_ORIGIN: str = "http://localhost:8000"

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_SOCIAL_YEARLY: str = ""
    STRIPE_PRICE_CORPORATE_YEARLY: str = ""
    STRIPE_PRICE_TAGLINK_MONTHLY: str = ""

    # SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = "Uwitz Cards <noreply@uwitz.cards>"

    # Entra ID (server-side only — client secret never exposed to browser)
    ENTRA_CLIENT_ID: str = "bf993ac3-0ca5-4172-9617-0e8851d5de1d"
    ENTRA_CLIENT_SECRET: str = ""
    ENTRA_TENANT_ID: str = "7625c8c5-0680-4ccc-8840-dc993791d475"

    # Entra ID Access Group Codes
    ENTRA_GROUP_LOGISTICS: str = "AGC001L"
    ENTRA_GROUP_ADMIN_FULL: str = "AGC001Z"
    ENTRA_GROUP_ADMIN_SUPER: str = "AGC001S"
    ENTRA_GROUP_USER_ELEVATED: str = "UE01Z"

    # Irys (server-side only)
    IRYS_CLIENT_ID: str = ""
    IRYS_CLIENT_SECRET: str = ""
    IRYS_TOKEN_URL: str = "https://irys.uwz/oauth/token"

    # Site
    SITE_URL: str = "http://localhost:8000"
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
