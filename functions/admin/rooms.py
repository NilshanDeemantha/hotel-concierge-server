"""Admin room saving: the `rooms` collection."""

import re

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from admin.guard import AdminError

# Must match the ROOMS names in src/content/home.ts of the web app.
ROOM_TYPES = ("Garden Room", "Sea View Deluxe", "Family Suite")
NUMBER = re.compile(r"[A-Za-z0-9-]{1,10}")
# Must match AMENITIES in src/content/admin.ts of the web app.
AMENITIES = (
    "Air conditioning", "Ceiling fan", "Wardrobe", "Wi-Fi", "TV", "Hot water",
    "Mini fridge", "Tea and coffee", "Safe", "Desk", "Balcony", "Sea view",
    "Bathtub", "Hair dryer", "Iron", "Garden view", "Pool view", "Private bathroom",
    "Free toiletries", "Towels", "Slippers", "Mosquito net", "Seating area", "Telephone",
    "Satellite channels", "Daily housekeeping", "Room service", "Laundry service (paid)", "Free parking", "Breakfast included",
    "Extra bed on request", "Pets allowed", "Non-smoking", "Smoking allowed",
)
MAX_PRICE = 10_000_000  # whole rupees per night
MAX_NOTICE = 300
MIN_PHOTOS, MAX_PHOTOS = 5, 10
ROOM_ID = re.compile(r"[A-Za-z0-9]{1,40}")
# The admin's browser uploads photos to rooms/<room id>/ in this bucket (see storage.rules); we only keep their download links.
PHOTO_PREFIX = "https://firebasestorage.googleapis.com/v0/b/thambili-bay-concierge.firebasestorage.app/o/rooms%2F"


def validate_room(body: dict) -> dict:
    room_id = body.get("id")
    if not isinstance(room_id, str) or not ROOM_ID.fullmatch(room_id):
        raise AdminError(400, "Invalid room id.")
    number, type_, active = body.get("number"), body.get("type"), body.get("active", True)
    if not isinstance(number, str) or not NUMBER.fullmatch(number):
        raise AdminError(400, "Room number must be 1 to 10 letters, digits or dashes.")
    name, amenities = body.get("name"), body.get("amenities", [])
    price, notice, photos = body.get("price"), body.get("notice", ""), body.get("photos")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise AdminError(400, "Room name must be 1 to 60 characters.")
    if type_ not in ROOM_TYPES:
        raise AdminError(400, "Unknown room type.")
    if not isinstance(amenities, list) or len(set(amenities)) != len(amenities) or any(a not in AMENITIES for a in amenities):
        raise AdminError(400, "Unknown or repeated feature.")
    if (not isinstance(photos, list) or not MIN_PHOTOS <= len(photos) <= MAX_PHOTOS
            or len(set(photos)) != len(photos)
            or any(not isinstance(p, str) or not p.startswith(f"{PHOTO_PREFIX}{room_id}%2F") or len(p) > 500 for p in photos)):
        raise AdminError(400, f"Add {MIN_PHOTOS} to {MAX_PHOTOS} different photos of this room.")
    if "Non-smoking" in amenities and "Smoking allowed" in amenities:
        raise AdminError(400, "A room cannot be both non-smoking and smoking allowed.")
    if type(price) is not int or not 0 < price <= MAX_PRICE:
        raise AdminError(400, "Price must be a whole number above 0.")
    if not isinstance(notice, str) or len(notice.strip()) > MAX_NOTICE:
        raise AdminError(400, f"Special notice can be at most {MAX_NOTICE} characters.")
    if not isinstance(active, bool):
        raise AdminError(400, "Active must be true or false.")
    return {
        "id": room_id, "number": number, "name": name.strip(), "type": type_, "amenities": amenities,
        "price": price, "notice": notice.strip(), "photos": photos, "active": active,
    }


def _room(id: str, data: dict) -> dict:
    return {"id": id, **data}


def save_room(body: dict) -> dict:
    fields = validate_room(body)
    room_id = fields.pop("id")
    rooms = firestore.client().collection("rooms")
    # shortcut: two admins saving the same number at once could both pass the check; fine for a few staff, move to a transaction if that changes.
    for hit in rooms.where(filter=FieldFilter("number", "==", fields["number"])).stream():
        if hit.id != room_id:
            raise AdminError(409, "Room number already in use.")
    # The browser picks the id before the first upload (photos live in rooms/<id>/), so an unknown id creates the room.
    ref = rooms.document(room_id)
    data = {**fields, "updatedAt": firestore.SERVER_TIMESTAMP}
    if ref.get().exists:
        ref.set(data, merge=True)
    else:
        ref.set({**data, "createdAt": firestore.SERVER_TIMESTAMP})
    return {"room": _room(room_id, fields)}
