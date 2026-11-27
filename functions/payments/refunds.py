"""A guest cancels a booking: the room is released and the refund their policy allows goes back to their card."""

import stripe
from firebase_admin import firestore

from messages import booking as bk
from payments import checkout, config, notices, policy, state


def quote_for(found: dict) -> dict:
    """What cancelling this booking now would do. Raises BookingError when it cannot be cancelled here."""
    status = found.get("status")
    if status == "payment_pending":
        return {"status": status, "refundLKR": 0, "note": "Nothing has been paid, so nothing is charged or refunded."}
    if status != "confirmed":
        raise bk.BookingError("This booking is not active, so there is nothing to cancel.")
    q = policy.quote(found["checkIn"], found["totalLKR"], bk.today())
    if q["daysBeforeCheckIn"] < 0:
        raise bk.BookingError("This stay has already started, so it cannot be cancelled here. Please contact reception.")
    return {"status": status, "totalLKR": found["totalLKR"], **q}


def cancel(db, found: dict) -> dict:
    """Cancels the guest's booking and refunds it. Raises BookingError when it cannot be cancelled here."""
    q = quote_for(found)
    booking_id = found["bookingId"]
    if q["status"] == "payment_pending":
        sid = found.get("checkoutSessionId")
        if sid:
            try:
                checkout.expire_session(sid)  # so the link cannot be paid after the cancellation
            except stripe.InvalidRequestError:
                pass  # already closed or paid: the cancel below re-checks the status
        if not state.cancel_unpaid(db, booking_id, "guest_request"):
            raise bk.BookingError("This booking is no longer waiting for payment.")
        return q
    refund = {"cancelReason": "guest_request", "refundLKR": q["refundLKR"], "refundPercent": q["refundPercent"], "refundStatus": "pending"}
    if state.cancel(db, booking_id, "guest_request", allowed=("confirmed",), extra=refund) is None:
        raise bk.BookingError("This booking has already been cancelled.")
    booking_ref = db.collection("bookings").document(booking_id)
    pay_ref = db.collection("payments").document(booking_id)
    try:
        r = stripe.Refund.create(
            payment_intent=found["paymentIntentId"], amount=config.minor_units(q["refundLKR"]), reason="requested_by_customer",
            metadata={"booking_id": booking_id}, api_key=config.STRIPE_SECRET_KEY.value, idempotency_key=f"guest-refund-{booking_id}",
        )
        booking_ref.update({"refundStatus": "done", "refundId": r.id})
        pay_ref.set({"status": "refunded" if q["refundPercent"] == 100 else "partially_refunded", "refundId": r.id,
                     "refundedAmountMinor": config.minor_units(q["refundLKR"]), "refundedAt": firestore.SERVER_TIMESTAMP}, merge=True)
        q["refunded"] = True
    except Exception:
        print(f"guest refund failed for booking {booking_id}")  # the cancellation stands: reception pays it back by hand
        import traceback

        traceback.print_exc()
        booking_ref.update({"refundStatus": "failed"})
        pay_ref.set({"status": "refund_failed"}, merge=True)
        q["refunded"] = False
    return q
