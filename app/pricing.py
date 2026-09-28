CARD_PRICING = {
    "social":    {"digital": 0.00, "physical": 35.00, "aluminium": 75.00},
    "corporate": {"digital": 0.00, "physical": 50.00, "aluminium": 90.00},
    "taglink":   {"digital": 0.00, "physical": 15.00},
}

# Physical card materials. "aluminium" is pre-order only; price replaces the
# standard "physical" (plastic) price for the selected card type.
CARD_MATERIALS = {
    "plastic":   {"label": "Plastic",   "pre_order": False},
    "aluminium": {"label": "Aluminium", "pre_order": True},
}

SUBSCRIPTION_PRICING = {
    "social_yearly":    {"amount": 25.00, "interval": "year"},
    "corporate_yearly": {"amount": 45.00, "interval": "year"},
    "taglink_monthly":  {"amount": 2.99,  "interval": "month"},
}

PLAN_DISPLAY_NAMES = {
    "social_yearly": "Social Yearly",
    "corporate_yearly": "Corporate Yearly",
    "taglink_monthly": "TagLink Monthly",
}


def plan_display_name(plan: str) -> str:
    return PLAN_DISPLAY_NAMES.get(plan, plan.replace("_", " ").title() if plan else "")

CONVERSION_PLAN_MAP = {
    ("social", "taglink"): "taglink_monthly",
    ("taglink", "social"): "social_yearly",
}

STRIPE_FEE_RATE = 0.03
STRIPE_FEE_MIN = 0.50


def calculate_stripe_fee(amount: float) -> float:
    fee = max(amount * STRIPE_FEE_RATE, STRIPE_FEE_MIN)
    return round(fee, 2)


def calculate_total(subtotal: float) -> tuple[float, float]:
    fee = calculate_stripe_fee(subtotal)
    return fee, round(subtotal + fee, 2)
