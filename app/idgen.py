import random
import time
import secrets
import string


def gen_user_id() -> str:
    """Generate user ID in format: {random_digits}.{unix_timestamp}"""
    rand_part = ''.join(random.choices(string.digits, k=10))
    ts_part = str(int(time.time()))
    return f"{rand_part}.{ts_part}"


def gen_card_id() -> str:
    """Generate 6-char alphanumeric card ID for URLs."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=6))


def gen_order_id() -> str:
    """Generate 10-char uppercase+digits order ID."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))


def gen_token() -> str:
    """Generate 40-char hex token for API auth."""
    return secrets.token_hex(20)


def gen_referral() -> str:
    """Generate 6-char hex referral code."""
    return secrets.token_hex(3).upper()


def gen_short_hex(n: int = 4) -> str:
    """Generate short hex string."""
    return secrets.token_hex(n)
