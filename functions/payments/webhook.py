"""The Stripe webhook and the expiry sweep: how a payment (or its absence) reaches the booking.

The webhook is the only way a booking becomes paid. Its signature is checked against the raw request body with
the endpoint's signing secret before anything is read, so nobody can fake a payment by calling the URL.
"""

from datetime import datetime, timezone

import stripe
from firebase_admin import firestore
from firebase_functions import https_fn, options, scheduler_fn

from common import json_response
from payments import config, state

MAX_BODY_BYTES = 256 * 1024  # a Stripe event is a few KB


def handle_event(db, event) -> str:
    """Acts on one verified event, once. Events for other things are ignored. Returns what was done."""
    done = db.collection("stripeEvents").document(event["id"])
    if done.get().exists:
        return "duplicate"
    session = event["data"]["object"]
    booking_id = session.get("client_reference_id")
    kind = event["type"]
    if not booking_id:
        outcome = "ignored"
    elif kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        # A completed session of a slow payment method is not paid yet: wait for async_payment_succeeded.
        outcome = state.mark_paid(db, session, event["id"]) if session.get("payment_status") == "paid" else "waiting"
    elif kind in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        reason = "payment_link_expired" if kind.endswith("expired") else "payment_failed"
        outcome = "cancelled" if state.cancel_unpaid(db, booking_id, reason) else "nothing_to_cancel"
    else:
        outcome = "ignored"
    done.set({"type": kind, "outcome": outcome, "bookingId": booking_id, "processedAt": firestore.SERVER_TIMESTAMP})
    return outcome


@https_fn.on_request(secrets=[config.STRIPE_SECRET_KEY, config.STRIPE_WEBHOOK_SECRET])
def stripe_webhook(req: https_fn.Request) -> https_fn.Response:
    """Receives Stripe events. Answers 400 to anything that is not a correctly signed event, 500 if handling failed
    (so Stripe retries), 200 otherwise."""
    if req.method != "POST":
        return json_response({"error": "Method not allowed"}, 405)
    if (req.content_length or 0) > MAX_BODY_BYTES:
        return json_response({"error": "Request too large"}, 413)
    try:
        event = stripe.Webhook.construct_event(  # the raw bytes: any change to the body breaks the signature
            req.get_data(), req.headers.get("Stripe-Signature", ""), config.STRIPE_WEBHOOK_SECRET.value
        ).to_dict()  # a Stripe object is not a dict: a plain dict is what the rest of the code reads
    except (ValueError, stripe.SignatureVerificationError):
        return json_response({"error": "Invalid signature"}, 400)
    # A test-mode event must never touch a live setup and the other way round.
    if bool(event.get("livemode")) != config.is_live_key(config.STRIPE_SECRET_KEY.value):
        return json_response({"received": True, "ignored": "mode"}, 200)
    try:
        outcome = handle_event(firestore.client(), event)
    except Exception:
        print(f"stripe_webhook failed for event {event.get('id')}")
        import traceback

        traceback.print_exc()
        return json_response({"error": "Could not process the event"}, 500)
    return json_response({"received": True, "outcome": outcome}, 200)


@scheduler_fn.on_schedule(
    schedule="every 30 minutes",
    secrets=[config.STRIPE_SECRET_KEY],
    retry_count=0,  # the next run is 30 minutes away
    memory=options.MemoryOption.MB_256,
)
def expire_unpaid_bookings(event: scheduler_fn.ScheduledEvent) -> None:
    """Cancels bookings whose payment time has run out and frees their rooms."""
    done = state.expire_overdue(firestore.client(), datetime.now(timezone.utc))
    print(f"expire_unpaid_bookings: {done}")
