CARD_PRICING = {
    "social":    {"physical": 35.00, "digital": 0.00},
    "corporate": {"physical": 50.00, "digital": 0.00},
    "taglink":   {"physical": 15.00, "digital": 0.00},
}

SUBSCRIPTION_PRICING = {
    "social_yearly":    {"amount": 25.00, "interval": "year"},
    "corporate_yearly": {"amount": 45.00, "interval": "year"},
    "taglink_monthly":  {"amount": 2.99,  "interval": "month"},
}

STRIPE_FEE_RATE = 0.03
STRIPE_FEE_MIN = 0.50


def calculate_stripe_fee(amount: float) -> float:
    fee = max(amount * STRIPE_FEE_RATE, STRIPE_FEE_MIN)
    return round(fee, 2)


def calculate_total(subtotal: float) -> tuple[float, float]:
    fee = calculate_stripe_fee(subtotal)
    return fee, round(subtotal + fee, 2)
