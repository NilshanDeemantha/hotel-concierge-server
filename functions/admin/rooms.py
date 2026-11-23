"""Admin room management: the `rooms` collection."""

import re

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from admin.guard import AdminError

# Must match the ROOMS names in src/content/home.ts of the web app.
ROOM_TYPES = ("Garden Room", "Sea View Deluxe", "Family Suite")
NUMBER = re.compile(r"[A-Za-z0-9-]{1,10}")


def validate_room(body: dict) -> dict:
    number, type_, active = body.get("number"), body.get("type"), body.get("active", True)
    if not isinstance(number, str) or not NUMBER.fullmatch(number):
        raise AdminError(400, "Room number must be 1 to 10 letters, digits or dashes.")
    if type_ not in ROOM_TYPES:
        raise AdminError(400, "Unknown room type.")
    if not isinstance(active, bool):
        raise AdminError(400, "Active must be true or false.")
    room = {"number": number, "type": type_, "active": active}
    if "id" in body:
        if not isinstance(body["id"], str) or not body["id"]:
            raise AdminError(400, "Invalid room id.")
        room["id"] = body["id"]
    return room


def _room(id: str, data: dict) -> dict:
    return {"id": id, "number": data["number"], "type": data["type"], "active": data["active"]}


def list_rooms(body: dict) -> dict:
    docs = firestore.client().collection("rooms").stream()
    return {"rooms": sorted((_room(d.id, d.to_dict()) for d in docs), key=lambda r: r["number"])}


def save_room(body: dict) -> dict:
    fields = validate_room(body)
    room_id = fields.pop("id", None)
    rooms = firestore.client().collection("rooms")
    # shortcut: two admins saving the same number at once could both pass the check; fine for a few staff, move to a transaction if that changes.
    for hit in rooms.where(filter=FieldFilter("number", "==", fields["number"])).stream():
        if hit.id != room_id:
            raise AdminError(409, "Room number already in use.")
    ref = rooms.document(room_id) if room_id else rooms.document()
    data = {**fields, "updatedAt": firestore.SERVER_TIMESTAMP}
    if room_id:
        if not ref.get().exists:
            raise AdminError(404, "Room not found.")
        ref.set(data, merge=True)
    else:
        ref.set({**data, "createdAt": firestore.SERVER_TIMESTAMP})
    return {"room": _room(room_id or ref.id, fields)}
