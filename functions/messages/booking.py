"""What Xenia can do with rooms: list them, find the free ones for some dates, and book one.

Rooms live in `rooms`, taken nights in `availability` (one document per room and month, see admin/availability.py)
and bookings in `bookings`. A booking marks its nights as booked and saves the booking in ONE transaction, so two
guests asking for the same room at the same moment cannot both get it: Firestore retries the loser, which then
sees the nights taken and gets a "no longer available" answer.
"""

import hashlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from admin import availability as av
from admin.guard import AdminError
from payments import config as pay

TZ = ZoneInfo("Asia/Colombo")
MAX_BOOKING_NIGHTS = 14
MAX_DAYS_AHEAD = 365
MAX_UPCOMING_PER_GUEST = 3  # a guest (or someone steering Xenia) cannot hold more bookings than this
MAX_ROOMS = 200
MAX_PHOTOS_PER_REPLY = 4  # photos sent in one message; the guest can ask for more
# Room photos are uploaded by staff to our own Storage bucket; nothing else is ever sent to a guest as an image.
PHOTO_URL_PREFIXES = ("https://firebasestorage.googleapis.com/v0/b/thambili-bay-concierge.firebasestorage.app/o/rooms%2F",)


class BookingError(Exception):
    """Something the guest can be told in words."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def today() -> date:
    return datetime.now(TZ).date()


def parse_stay(check_in: str, check_out: str) -> list[date]:
    """The nights of a stay: check-in up to, not including, check-out. Raises BookingError."""
    try:
        nights = av.parse_nights(check_in, check_out)
    except AdminError as e:
        raise BookingError(e.message)
    if nights[0] < today():
        raise BookingError("Check-in cannot be in the past.")
    if nights[0] > today() + timedelta(days=MAX_DAYS_AHEAD):
        raise BookingError(f"Bookings open up to {MAX_DAYS_AHEAD} days ahead.")
    if len(nights) > MAX_BOOKING_NIGHTS:
        raise BookingError(f"A booking can be at most {MAX_BOOKING_NIGHTS} nights.")
    return nights


def _room_view(room_id: str, r: dict) -> dict:
    return {
        "roomId": room_id,
        "number": r.get("number", ""),
        "name": r.get("name", ""),
        "type": r.get("type", ""),
        "pricePerNightLKR": r.get("price", 0),
        "features": r.get("amenities", []),
        "checkInTime": r.get("checkIn", "14:00"),
        "checkOutTime": r.get("checkOut", "11:00"),
        "notice": r.get("notice", ""),
    }


def _active_rooms(db) -> list[tuple[str, dict]]:
    rooms = [(d.id, d.to_dict() or {}) for d in db.collection("rooms").limit(MAX_ROOMS).stream()]
    return sorted((r for r in rooms if r[1].get("active")), key=lambda r: r[1].get("number", ""))


def resolve_room(db, ref: str) -> str:
    """The room id for what the guest and Xenia call the room: its number (best), its name or its id.

    Xenia only sees earlier messages as text, not the tool results, so she books by the room number she told the guest.
    """
    key = (ref or "").strip().lower()
    rooms = _active_rooms(db)
    found = [i for i, r in rooms if key in (i.lower(), str(r.get("number", "")).lower())]
    found = found or [i for i, r in rooms if key and key == str(r.get("name", "")).lower()]
    if not found:
        raise BookingError("I could not find that room. Check the available rooms again and use the room number.")
    if len(found) > 1:
        raise BookingError("More than one room has that name. Please use the room number.")
    return found[0]


def room_photos(db, ref: str, already_sent: frozenset | set = frozenset()) -> dict:
    """Up to MAX_PHOTOS_PER_REPLY photos of the room that the guest has not been sent yet. Raises BookingError."""
    room_id = resolve_room(db, ref)
    room = db.collection("rooms").document(room_id).get().to_dict() or {}
    urls = [u for u in room.get("photos", []) if isinstance(u, str) and u.startswith(PHOTO_URL_PREFIXES)]
    if not urls:
        raise BookingError("There are no photos of that room yet.")
    unseen = [u for u in urls if u not in already_sent]
    if not unseen:
        raise BookingError("You have already been sent all the photos of that room.")
    chosen = unseen[:MAX_PHOTOS_PER_REPLY]
    return {"room": room.get("name") or room.get("type", ""), "number": room.get("number", ""), "urls": chosen,
            "total": len(urls), "remaining": len(unseen) - len(chosen)}


def list_rooms(db) -> dict:
    """Every room that can be booked, with its type, price, features and times, grouped by type."""
    by_type: dict[str, list] = {}
    for room_id, r in _active_rooms(db):
        by_type.setdefault(r.get("type", "Other"), []).append(_room_view(room_id, r))
    return {"roomTypes": by_type}


def _taken_days(db, nights: list[date]) -> dict[str, dict]:
    months = sorted({f"{n:%Y-%m}" for n in nights})
    found = db.collection("availability").where(filter=FieldFilter("month", "in", months)).stream()
    return {d.id: (d.to_dict() or {}).get("days", {}) for d in found}


def find_rooms(db, check_in: str, check_out: str, room_type: str = "") -> dict:
    """The rooms free on every night of the stay, with the price of the stay."""
    nights = parse_stay(check_in, check_out)
    taken = _taken_days(db, nights)
    free = []
    for room_id, r in _active_rooms(db):
        if room_type and r.get("type", "").lower() != room_type.strip().lower():
            continue
        if any(av.day_key(n) in taken.get(av.doc_id(room_id, n), {}) for n in nights):
            continue
        free.append({**_room_view(room_id, r), "totalLKR": r.get("price", 0) * len(nights)})
    return {"checkIn": check_in, "checkOut": check_out, "nights": len(nights), "freeRooms": free}


def booking_id(message_id: str, room_id: str, check_in: str, check_out: str) -> str:
    """The same ask in the same message always gives the same id, so a repeat cannot book twice."""
    return hashlib.sha1(f"{message_id}|{room_id}|{check_in}|{check_out}".encode()).hexdigest()[:20]


def _commit(transaction, db, booking_ref, room_id: str, nights: list[date], base: dict) -> dict:
    """Runs inside the transaction (and again if Firestore retries it): every read first, then the writes."""
    room = db.collection("rooms").document(room_id).get(transaction=transaction)
    if not room.exists or not (room.to_dict() or {}).get("active"):
        raise BookingError("That room cannot be booked.")
    done = booking_ref.get(transaction=transaction)
    if done.exists:
        return {**done.to_dict(), "alreadyBooked": True}
    refs = {i: db.collection("availability").document(i) for i in dict.fromkeys(av.doc_id(room_id, n) for n in nights)}
    existing = {}
    for i, ref in refs.items():
        snap = ref.get(transaction=transaction)
        existing[i] = (snap.to_dict() or {}).get("days", {}) if snap.exists else {}
    r = room.to_dict()
    booking = {
        **base,
        "roomNumber": r.get("number", ""), "roomName": r.get("name", ""), "roomType": r.get("type", ""),
        "pricePerNightLKR": r.get("price", 0), "totalLKR": r.get("price", 0) * len(nights),
        "checkInTime": r.get("checkIn", "14:00"), "checkOutTime": r.get("checkOut", "11:00"),
    }
    note = f"Booking {booking['reference']}, {booking['guestName']}"[:200]
    try:
        plan = av.plan_changes(existing, room_id, nights, "booked", note)
    except AdminError as e:
        raise BookingError(f"{e.message} Please choose another room or other dates.")
    for i, days in plan.items():
        for entry in days.values():
            entry["bookingId"] = booking_ref.id  # so cancelling an unpaid booking frees only its own nights
        transaction.set(refs[i], {"roomId": room_id, "month": i[-7:], "days": days}, merge=True)
    transaction.set(booking_ref, {**booking, "createdAt": firestore.SERVER_TIMESTAMP})
    return booking


def book_room(db, *, uid: str, conversation_id: str, message_id: str, room_id: str, check_in: str, check_out: str) -> dict:
    """Books the room for the stay in one transaction. Raises BookingError with words for the guest."""
    nights = parse_stay(check_in, check_out)
    # shortcut: counted outside the transaction, so two bookings at the same instant could pass; the cap is an
    # abuse guard, not a promise. The nights themselves are checked inside the transaction.
    mine = db.collection("bookings").where(filter=FieldFilter("uid", "==", uid)).stream()
    upcoming = [
        b for b in mine
        if (b.to_dict() or {}).get("checkOut", "") >= f"{today():%Y-%m-%d}" and (b.to_dict() or {}).get("status") != "cancelled"
    ]
    bid = booking_id(message_id, room_id, check_in, check_out)
    others = {b.id for b in upcoming} - {bid}
    if len(others) >= MAX_UPCOMING_PER_GUEST:
        raise BookingError("You already have the most bookings I can make for one guest.")
    if sum(1 for b in upcoming if b.id != bid and (b.to_dict() or {}).get("status") == "payment_pending") >= pay.MAX_PENDING_PER_GUEST:
        raise BookingError("You have bookings waiting for payment. Please pay for them, or let them expire, before booking more.")
    profile = db.collection("users").document(uid).get()
    base = {
        "uid": uid, "conversationId": conversation_id, "roomId": room_id,
        "guestName": ((profile.to_dict() or {}).get("name") or "Guest")[:80] if profile.exists else "Guest",
        "checkIn": check_in, "checkOut": check_out, "nights": len(nights),
        # Held for PAYMENT_WINDOW: it becomes "confirmed" when the payment arrives, "cancelled" if it does not.
        "status": "payment_pending", "paymentDeadline": datetime.now(timezone.utc) + pay.PAYMENT_WINDOW,
        "bookingId": bid, "reference": f"TB-{bid[:6].upper()}",
    }
    # Wrapped per call: the decorator object keeps retry state, so sharing it across concurrent calls is unsafe.
    return firestore.transactional(_commit)(
        db.transaction(), db, db.collection("bookings").document(bid), room_id, nights, base
    )


def _long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%B %Y}"


def find_guest_booking(db, uid: str, reference: str) -> dict:
    """The guest's own booking with this reference. Raises BookingError."""
    key = (reference or "").strip().upper()
    for d in db.collection("bookings").where(filter=FieldFilter("uid", "==", uid)).stream():
        if (d.to_dict() or {}).get("reference") == key:
            return {**d.to_dict(), "bookingId": d.id}
    raise BookingError("I could not find a booking with that reference.")


def confirmation_text(b: dict) -> str:
    """The message sent to the guest after a booking. Written here, not by the model, so the details are exact."""
    return (
        f"Your room is reserved, and will be confirmed when you pay. Reference {b['reference']}: {b['roomName'] or b['roomType']} "
        f"({b['roomType']}, room {b['roomNumber']}) for {b['nights']} {'night' if b['nights'] == 1 else 'nights'}, "
        f"{_long_date(b['checkIn'])} to {_long_date(b['checkOut'])}, LKR {b['totalLKR']:,} in total "
        f"(LKR {b['pricePerNightLKR']:,} per night). "
        f"Check-in is from {b['checkInTime']} on {_long_date(b['checkIn'])} and check-out is by {b['checkOutTime']} "
        f"on {_long_date(b['checkOut'])}. The room is held until {pay.format_time(b['paymentDeadline'])}: pay with the "
        "payment link in my next message before then, or the booking is cancelled automatically. "
        "Please keep your reference."
    )
