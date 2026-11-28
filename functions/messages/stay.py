"""The guest's own bookings as a few plain lines for Xenia's prompt.

The chat history sent to the model is short, so a room or date mentioned earlier is forgotten. Read from the bookings
themselves on every reply, this is always current (a cancellation drops out at once) and costs one query. Only what is
useful during and before a stay goes in: no past or cancelled bookings, no card or payment details.
"""

from datetime import datetime

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from messages import booking
from messages.dining_request import _moment, now

MAX_BOOKINGS = booking.MAX_UPCOMING_PER_GUEST
LIVE = ("confirmed", "payment_pending")


def _line(b: dict, at: datetime) -> str:
    start, end = _moment(b.get("checkIn"), b.get("checkInTime"), "14:00"), _moment(b.get("checkOut"), b.get("checkOutTime"), "11:00")
    state = "paid and confirmed" if b["status"] == "confirmed" else "reserved, waiting for payment"
    if b["status"] == "confirmed" and start and end and start <= at <= end:
        state = "the guest is staying now"
    return (
        f"- {b.get('reference')}: {b.get('roomName') or b.get('roomType')} (room {b.get('roomNumber')}), "
        f"{b.get('checkIn')} to {b.get('checkOut')}, {b.get('nights')} nights, LKR {b.get('totalLKR', 0):,}, {state}. "
        f"Check-in from {b.get('checkInTime')}, check-out by {b.get('checkOutTime')}."
    )


def context(uid: str, db=None, at: datetime | None = None) -> str:
    """The guest's current and upcoming bookings, soonest first, or "" when there are none. Never raises: Xenia can answer without it."""
    at = at or now()
    try:
        db = db or firestore.client()
        found = [d.to_dict() or {} for d in db.collection("bookings").where(filter=FieldFilter("uid", "==", uid)).stream()]
    except Exception:
        print("stay.context: could not read the bookings")
        return ""
    live = sorted((b for b in found if b.get("status") in LIVE and b.get("checkOut", "") >= f"{at:%Y-%m-%d}"), key=lambda b: b.get("checkIn", ""))
    if not live:
        # Said outright, so a guest claiming a booking (or a note saying so) is not believed; a failed read above says nothing.
        return "The guest has no current or upcoming bookings (real data, always right). If they say they have one, it is not on their account: they can book with you now, or contact reception."
    return ("The guest's own bookings (real data, always right: use it for their room, dates and status, even when the chat says otherwise):\n"
            + "\n".join(_line(b, at) for b in live[:MAX_BOOKINGS]))
