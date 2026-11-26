"""Booking payment states: payment_pending -> confirmed (paid) or cancelled (not paid in time).

Every change is a Firestore transaction that checks the current state first, so a repeated webhook, the
expiry sweep and a payment arriving at the last second can never confirm or cancel a booking twice.
"""

import stripe
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from admin import availability as av
from payments import checkout, config, notices


def _paid_tx(transaction, db, booking_ref, session, event_id) -> tuple[str, dict]:
    snap = booking_ref.get(transaction=transaction)
    if not snap.exists:
        return "unknown", {}
    b = snap.to_dict()
    # Never trust the metadata alone: the session must be the one we made, for the amount we asked, in our currency.
    # (A booking without a stored session id is one whose id was lost after Stripe made the session: the amount,
    # currency and booking id still have to match, and the id is stored now.)
    if (
        b.get("checkoutSessionId") not in (None, session["id"])
        or session.get("amount_total") != config.minor_units(b["totalLKR"])
        or (session.get("currency") or "").lower() != config.CURRENCY
    ):
        return "mismatch", b
    if b["status"] == "payment_pending":
        transaction.update(booking_ref, {
            "status": "confirmed", "paidAt": firestore.SERVER_TIMESTAMP, "paymentIntentId": session.get("payment_intent"),
            "checkoutSessionId": session["id"],
        })
        transaction.set(db.collection("payments").document(booking_ref.id), {
            "status": "paid", "paidAt": firestore.SERVER_TIMESTAMP, "paymentIntentId": session.get("payment_intent"),
            "checkoutSessionId": session["id"], "paymentStatus": session.get("payment_status"),
            # What Stripe says was paid (checked above against what we asked for), and by whom: no card data.
            "amountPaidMinor": session.get("amount_total"), "currency": session.get("currency"),
            "customerEmail": (session.get("customer_details") or {}).get("email"),
            "paymentMethodTypes": session.get("payment_method_types"), "stripeEventId": event_id,
        }, merge=True)
        return "confirmed_now", b
    return b["status"], b


def mark_paid(db, session, event_id: str | None = None) -> str:
    """The guest paid this Checkout Session: confirm the booking. Returns what happened, for logs and tests."""
    booking_ref = db.collection("bookings").document(session.get("client_reference_id") or "none")
    outcome, b = firestore.transactional(_paid_tx)(db.transaction(), db, booking_ref, session, event_id)
    if outcome == "confirmed_now":
        notices.paid(b)
    elif outcome == "cancelled" and not b.get("refundedAt"):
        # Paid after the booking was cancelled and the room released: give the money back.
        refund = stripe.Refund.create(
            payment_intent=session.get("payment_intent"), api_key=config.STRIPE_SECRET_KEY.value,
            idempotency_key=f"refund-{booking_ref.id}",
        )
        booking_ref.update({"refundedAt": firestore.SERVER_TIMESTAMP, "paymentIntentId": session.get("payment_intent")})
        db.collection("payments").document(booking_ref.id).set(
            {"status": "refunded", "refundedAt": firestore.SERVER_TIMESTAMP, "paymentIntentId": session.get("payment_intent"),
             "refundId": refund.id, "amountPaidMinor": session.get("amount_total")}, merge=True)
        notices.refunded(b)
        outcome = "refunded"
    elif outcome == "mismatch":
        print(f"payment mismatch for booking {booking_ref.id}: not confirmed")  # for a person to look at
        booking_ref.update({"paymentIssue": "The paid amount or session did not match the booking."})
        db.collection("payments").document(booking_ref.id).set({"status": "mismatch"}, merge=True)
    return outcome


def _cancel_tx(transaction, db, booking_ref, reason: str) -> dict | None:
    snap = booking_ref.get(transaction=transaction)
    if not snap.exists or snap.to_dict().get("status") != "payment_pending":
        return None  # paid, already cancelled or unknown: nothing to undo
    b = snap.to_dict()
    nights = av.parse_nights(b["checkIn"], b["checkOut"])
    refs = {i: db.collection("availability").document(i) for i in dict.fromkeys(av.doc_id(b["roomId"], n) for n in nights)}
    months = {i: r.get(transaction=transaction) for i, r in refs.items()}  # every read before the first write
    for i, ref in refs.items():
        days = (months[i].to_dict() or {}).get("days", {}) if months[i].exists else {}
        # Only the nights this booking took: a night that was removed or booked by someone else is left alone.
        mine = {k: firestore.DELETE_FIELD for k, entry in days.items() if entry.get("bookingId") == booking_ref.id}
        if mine:
            transaction.set(ref, {"days": mine}, merge=True)
    transaction.set(db.collection("payments").document(booking_ref.id), {"status": "expired"}, merge=True)
    transaction.update(booking_ref, {"status": "cancelled", "cancelledAt": firestore.SERVER_TIMESTAMP, "cancelReason": reason})
    return b


def cancel_unpaid(db, booking_id: str, reason: str) -> bool:
    """Cancels a booking that is still waiting for payment and frees its nights. False if there was nothing to cancel."""
    booking_ref = db.collection("bookings").document(booking_id)
    b = firestore.transactional(_cancel_tx)(db.transaction(), db, booking_ref, reason)
    if b is None:
        return False
    notices.cancelled(b)
    return True


def expire_overdue(db, now) -> dict:
    """The sweep: cancel every booking whose time to pay has run out. The deadline is ours, not Stripe's webhook."""
    done = {"cancelled": 0, "paid": 0}
    for doc in db.collection("bookings").where(filter=FieldFilter("status", "==", "payment_pending")).stream():
        b = doc.to_dict() or {}
        if b.get("paymentDeadline") is None or b["paymentDeadline"] > now:
            continue
        sid = b.get("checkoutSessionId")
        if sid:
            try:
                checkout.expire_session(sid)
            except stripe.InvalidRequestError:
                session = stripe.checkout.Session.retrieve(sid, api_key=config.STRIPE_SECRET_KEY.value)
                if session.payment_status == "paid":  # paid at the last moment: confirm instead of cancelling
                    mark_paid(db, session.to_dict())
                    done["paid"] += 1
                    continue
        if cancel_unpaid(db, doc.id, "payment_not_received"):
            done["cancelled"] += 1
    return done
