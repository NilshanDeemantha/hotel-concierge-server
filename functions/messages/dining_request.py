"""A guest's dining request: the order, where it goes (their room or the restaurant) and what it costs, saved for staff.

The dishes are checked by dining.validate_order (one meal, one dish per course, only dishes on the menu). A request is
accepted only from a guest whose confirmed booking covers the present moment (from check-in time on the first day to
check-out time on the last, Sri Lanka time), so the room is the one on that booking, never a room the guest names.
A request is saved once per guest message (the id is fixed), so a repeated task cannot order twice.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from firebase_admin import firestore
from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter

from messages import booking, chat, dining
from messages.dining import DiningError

SERVICES = {"room": "brought to your room", "restaurant": "served at the restaurant"}
MAX_OPEN_PER_GUEST = 3  # requests still waiting for the kitchen: an abuse guard, not a promise
RETENTION = timedelta(days=90)


def now() -> datetime:
    return datetime.now(booking.TZ)


def _moment(day: str, clock: str, fallback: str) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{day}T{clock or fallback}").replace(tzinfo=booking.TZ)
    except (TypeError, ValueError):
        return None


def active_stay(db, uid: str, at: datetime) -> dict | None:
    """The guest's confirmed booking whose stay (check-in time on the first day to check-out time on the last) includes `at`."""
    for d in db.collection("bookings").where(filter=FieldFilter("uid", "==", uid)).stream():
        b = d.to_dict() or {}
        start, end = _moment(b.get("checkIn"), b.get("checkInTime"), "14:00"), _moment(b.get("checkOut"), b.get("checkOutTime"), "11:00")
        if b.get("status") == "confirmed" and start and end and start <= at <= end:
            return {**b, "bookingId": d.id}
    return None


def request_id(message_id: str) -> str:
    return f"{message_id}-dining"


def create(db, *, uid: str, conversation_id: str, message_id: str, meal: str, dishes: list, service: str) -> dict:
    """Saves the request after checking everything again (the guest's yes is not trusted to have been checked).
    Raises DiningError with words for the guest."""
    service = (service or "").strip().lower()
    if service not in SERVICES:
        raise DiningError("Please tell me whether the food should be brought to your room or whether you will come to the restaurant.")
    order = dining.validate_order(db, meal, dishes)
    stay = active_stay(db, uid, now())
    if not stay:
        raise DiningError("Dining requests are for guests during their stay, and I could not find a confirmed booking on your "
                          "account that covers right now. If you are staying with us, please contact reception.")
    ref = db.collection("diningRequests").document(request_id(message_id))
    done = ref.get()
    if done.exists:
        return {**done.to_dict(), "alreadyRequested": True}
    open_now = [d for d in db.collection("diningRequests").where(filter=FieldFilter("uid", "==", uid)).stream()
                if (d.to_dict() or {}).get("status") == "requested"]
    if len(open_now) >= MAX_OPEN_PER_GUEST:
        raise DiningError("You already have several dining requests waiting. Please wait for those, or ask reception.")
    profile = db.collection("users").document(uid).get()
    request = {
        "uid": uid, "conversationId": conversation_id, "messageId": message_id,
        "guestName": ((profile.to_dict() or {}).get("name") or "Guest")[:80] if profile.exists else "Guest",
        "reference": "DR-" + hashlib.sha1(ref.id.encode()).hexdigest()[:6].upper(),
        "meal": order["meal"], "service": service, "items": order["items"], "totalLKR": order["totalLKR"],
        "roomId": stay["roomId"], "roomNumber": stay["roomNumber"],
        "bookingId": stay["bookingId"], "bookingReference": stay["reference"],
        "status": "requested",
    }
    try:
        ref.create({**request, "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP,
                    "expireAt": datetime.now(timezone.utc) + RETENTION})
    except AlreadyExists:
        return {**ref.get().to_dict(), "alreadyRequested": True}
    return request


def request_text(r: dict) -> str:
    """The message the guest gets. Written here, not by the model, so the dishes and the total are exact."""
    dishes = "; ".join(f"{i['name']} (LKR {i['priceLKR']:,})" for i in r["items"])
    where = f"will be brought to room {r['roomNumber']}" if r["service"] == "room" else "will be served at the restaurant"
    return (f"Your dining request {r['reference']} is received: {dishes}. Total LKR {r['totalLKR']:,}. It {where}. "
            "If you need to change anything, please contact reception.")


def send(message: dict, message_id: str, r: dict) -> None:
    try:
        chat.save_notice(message["conversationId"], message["uid"], f"{request_id(message_id)}-message", request_text(r))
    except AlreadyExists:
        pass
