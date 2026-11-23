"""The two admin write functions. Each hands the request to the guard with its handler.

Admins read conversations, messages, users, rooms and availability straight from Firestore (see firestore.rules).
"""

from firebase_functions import https_fn

from admin import availability, rooms
from admin.guard import run
from common import POST_ONLY_CORS


@https_fn.on_request(cors=POST_ONLY_CORS)
def admin_save_room(req: https_fn.Request) -> https_fn.Response:
    return run(req, rooms.save_room)


@https_fn.on_request(cors=POST_ONLY_CORS)
def admin_set_availability(req: https_fn.Request) -> https_fn.Response:
    return run(req, availability.set_availability)
