"""Admin room availability: the `availability` collection, one doc per room and month."""

import re
from datetime import date, timedelta

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from admin.guard import AdminError

MAX_NIGHTS = 60
STATUSES = ("booked", "blocked", "free")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def _date(s) -> date:
    try:
        if isinstance(s, str) and DATE.fullmatch(s):
            return date.fromisoformat(s)
    except ValueError:
        pass
    raise AdminError(400, "Dates must be valid YYYY-MM-DD.")


def parse_nights(start: str, end: str) -> list[date]:
    first, last = _date(start), _date(end)
    if first >= last:
        raise AdminError(400, "End date must be after start date.")
    if (last - first).days > MAX_NIGHTS:
        raise AdminError(400, f"At most {MAX_NIGHTS} nights at a time.")
    return [first + timedelta(days=i) for i in range((last - first).days)]


def doc_id(room_id: str, d: date) -> str:
    return f"{room_id}_{d:%Y-%m}"


def day_key(d: date) -> str:
    return f"{d:%d}"


def plan_changes(existing: dict[str, dict], room_id: str, nights: list[date], status: str,
                 note: str | None) -> dict[str, dict[str, dict | None]]:
    plan: dict[str, dict[str, dict | None]] = {}
    for d in nights:
        taken = day_key(d) in existing.get(doc_id(room_id, d), {})
        if status == "free":
            if taken:
                plan.setdefault(doc_id(room_id, d), {})[day_key(d)] = None
            continue
        if taken:
            raise AdminError(409, f"Room is already taken on {d:%Y-%m-%d}.")
        entry = {"status": status, **({"note": note} if note else {})}
        plan.setdefault(doc_id(room_id, d), {})[day_key(d)] = entry
    return plan


def get_availability(body: dict) -> dict:
    month = body.get("month")
    if not isinstance(month, str) or not MONTH.fullmatch(month):
        raise AdminError(400, "Month must be YYYY-MM.")
    docs = firestore.client().collection("availability").where(filter=FieldFilter("month", "==", month)).stream()
    return {"month": month, "days": {(data := d.to_dict())["roomId"]: data.get("days", {}) for d in docs}}


def _apply(transaction, db, room_id: str, nights: list[date], status: str, note: str | None) -> None:
    refs = {i: db.collection("availability").document(i) for i in dict.fromkeys(doc_id(room_id, d) for d in nights)}
    existing = {}
    for i, ref in refs.items():
        snap = ref.get(transaction=transaction)
        existing[i] = snap.to_dict().get("days", {}) if snap.exists else {}
    for i, days in plan_changes(existing, room_id, nights, status, note).items():
        days = {k: firestore.DELETE_FIELD if v is None else v for k, v in days.items()}
        transaction.set(refs[i], {"roomId": room_id, "month": i[-7:], "days": days}, merge=True)


def set_availability(body: dict) -> dict:
    room_id, status, note = body.get("roomId"), body.get("status"), body.get("note")
    if status not in STATUSES:
        raise AdminError(400, "Status must be booked, blocked or free.")
    if note is not None and (not isinstance(note, str) or len(note) > 200):
        raise AdminError(400, "Note must be text of at most 200 characters.")
    if not isinstance(room_id, str) or not room_id:
        raise AdminError(400, "Invalid room id.")
    nights = parse_nights(body.get("from"), body.get("to"))
    db = firestore.client()
    room = db.collection("rooms").document(room_id).get()
    if not room.exists:
        raise AdminError(404, "Room not found.")
    if status != "free" and not room.to_dict().get("active"):
        raise AdminError(409, "Room is inactive.")
    # Wrapped per call: the decorator object keeps retry state, so sharing it across concurrent requests is unsafe.
    firestore.transactional(_apply)(db.transaction(), db, room_id, nights, status, note)
    return {"nights": len(nights)}
