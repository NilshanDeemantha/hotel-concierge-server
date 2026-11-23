"""The six admin functions. Each hands the request to the guard with its handler."""

from firebase_functions import https_fn

from admin import availability, conversations, rooms
from admin.guard import run
from common import POST_ONLY_CORS


@https_fn.on_request(cors=POST_ONLY_CORS)
def admin_list_conversations(req: https_fn.Request) -> https_fn.Response:
    return run(req, conversations.list_conversations)


@https_fn.on_request(cors=POST_ONLY_CORS)
def admin_get_messages(req: https_fn.Request) -> https_fn.Response:
    return run(req, conversations.get_messages)


@https_fn.on_request(cors=POST_ONLY_CORS)
def admin_list_rooms(req: https_fn.Request) -> https_fn.Response:
    return run(req, rooms.list_rooms)


@https_fn.on_request(cors=POST_ONLY_CORS)
def admin_save_room(req: https_fn.Request) -> https_fn.Response:
    return run(req, rooms.save_room)


@https_fn.on_request(cors=POST_ONLY_CORS)
def admin_get_availability(req: https_fn.Request) -> https_fn.Response:
    return run(req, availability.get_availability)


@https_fn.on_request(cors=POST_ONLY_CORS)
def admin_set_availability(req: https_fn.Request) -> https_fn.Response:
    return run(req, availability.set_availability)
