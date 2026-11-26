"""Settings and small helpers for card payments with Stripe Checkout (one-time payments only).

Card details never reach our servers: the guest pays on a page hosted by Stripe. We keep two secrets in Secret
Manager (set with `firebase functions:secrets:set`, never in code): the API key and the webhook signing secret.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from firebase_functions.params import SecretParam

STRIPE_SECRET_KEY = SecretParam("STRIPE_SECRET_KEY")  # sk_test_... while testing
STRIPE_WEBHOOK_SECRET = SecretParam("STRIPE_CHECKOUT_WEBHOOK_KEY")  # whsec_..., from the webhook endpoint in the Stripe dashboard

# Where Stripe sends the guest after paying or leaving. Fixed here, never taken from a guest or the model.
SITE_URL = "https://thambili-bay-concierge.web.app"
CHECKOUT_HOST = "https://checkout.stripe.com/"

CURRENCY = "lkr"  # keep in step with MODEL_CONFIG.currency in the web app
MINOR_UNITS = 100  # Stripe amounts are in the smallest unit: LKR has two decimals
PAYMENT_WINDOW = timedelta(minutes=30)  # how long a booking is held unpaid
STRIPE_MIN_LIFETIME = timedelta(minutes=30, seconds=30)  # Stripe refuses a session that lives under 30 minutes; 30 s of margin for the call
MAX_PENDING_PER_GUEST = 2  # unpaid bookings one guest can hold at once, so rooms cannot be hoarded

TZ = ZoneInfo("Asia/Colombo")


def minor_units(amount: int) -> int:
    """Whole rupees to the amount Stripe wants."""
    return amount * MINOR_UNITS


def is_live_key(key: str) -> bool:
    return key.startswith(("sk_live_", "rk_live_"))


def format_time(when: datetime) -> str:
    """A moment as the guest reads it, in hotel time."""
    local = when.astimezone(TZ)
    return f"{local:%H:%M} on {local.day} {local:%B %Y} (Sri Lanka time)"
