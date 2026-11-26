"""Stripe Checkout Sessions for bookings: create the payment link, fetch it again, expire it."""

from datetime import datetime, timezone

import stripe
from firebase_admin import auth, firestore

from payments import config


class PaymentError(Exception):
    """Something the guest can be told in words."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _guest_email(uid: str) -> str | None:
    try:
        return auth.get_user(uid).email
    except Exception:
        return None  # the receipt email is optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline(booking: dict) -> datetime:
    return booking["paymentDeadline"].astimezone(timezone.utc)


def create_session(db, booking: dict) -> dict:
    """The Checkout Session for a booking that is waiting for payment.

    The price comes from the booking (which the server worked out from the room), never from the guest or the
    model. The idempotency key makes a retry give back the same session instead of a second link.
    """
    booking_id, deadline = booking["bookingId"], _deadline(booking)
    email = _guest_email(booking["uid"])
    session = stripe.checkout.Session.create(
        mode="payment",
        submit_type="pay",
        client_reference_id=booking_id,
        **({"customer_email": email} if email else {}),
        line_items=[{
            "quantity": 1,
            "price_data": {
                "currency": config.CURRENCY,
                "unit_amount": config.minor_units(booking["totalLKR"]),
                "product_data": {
                    "name": f"{booking['roomName'] or booking['roomType']} ({booking['roomType']}), room {booking['roomNumber']}",
                    "description": f"{booking['checkIn']} to {booking['checkOut']}, {booking['nights']} night(s). Booking {booking['reference']}",
                },
            },
        }],
        metadata={"booking_id": booking_id, "reference": booking["reference"]},
        payment_intent_data={
            "description": f"Thambili Bay booking {booking['reference']}",
            "metadata": {"booking_id": booking_id, "reference": booking["reference"]},
        },
        # Stripe needs at least 30 minutes; the booking deadline (and the sweep that closes the session) is ours.
        expires_at=int(max(deadline, _now() + config.STRIPE_MIN_LIFETIME).timestamp()),
        success_url=f"{config.SITE_URL}/payment/success?ref={booking['reference']}",
        cancel_url=f"{config.SITE_URL}/payment/cancelled?ref={booking['reference']}",
        api_key=config.STRIPE_SECRET_KEY.value,
        idempotency_key=f"checkout-{booking_id}",
    )
    db.collection("bookings").document(booking_id).update({"checkoutSessionId": session.id})
    # What we know about the payment, kept apart from the booking. Never card data: that stays with Stripe.
    db.collection("payments").document(booking_id).set({
        "bookingId": booking_id, "uid": booking["uid"], "reference": booking["reference"],
        "checkoutSessionId": session.id, "amountLKR": booking["totalLKR"], "amountMinor": config.minor_units(booking["totalLKR"]),
        "currency": config.CURRENCY, "status": "pending", "expiresAt": deadline, "createdAt": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    return {"url": session.url, "expiresAt": deadline.isoformat(), "sessionId": session.id}


def link_for(db, booking: dict) -> dict:
    """The payment link of a booking: the one already made while it is open, a new one if none was made yet.

    Raises PaymentError when nothing can be paid any more.
    """
    if booking.get("status") != "payment_pending":
        raise PaymentError("This booking is not waiting for payment.")
    if _deadline(booking) <= _now():
        raise PaymentError("The time to pay for this booking has run out, so it is being cancelled.")
    session_id = booking.get("checkoutSessionId")
    if not session_id:
        return create_session(db, booking)
    session = stripe.checkout.Session.retrieve(session_id, api_key=config.STRIPE_SECRET_KEY.value)
    if session.status != "open" or not (session.url or "").startswith(config.CHECKOUT_HOST):
        raise PaymentError("The payment link is no longer open.")
    return {"url": session.url, "expiresAt": _deadline(booking).isoformat(), "sessionId": session.id}


def expire_session(session_id: str) -> None:
    """Closes an open session so the link stops working. Raises stripe.InvalidRequestError if it is not open."""
    stripe.checkout.Session.expire(session_id, api_key=config.STRIPE_SECRET_KEY.value)
