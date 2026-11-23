"""Entry point. The Firebase CLI deploys every function exported here.

Each function is defined in its own folder: users/ (sign-up), messages/ (chat) and admin/ (staff dashboard writes).
"""

import firebase_admin
from firebase_functions import options

firebase_admin.initialize_app()

# Cost guard: at most one small instance, in the region next to the Firestore database.
options.set_global_options(
    region="asia-south1",
    max_instances=1,
    memory=options.MemoryOption.MB_256,
)

# These imports stay below the options above: a function reads the global options when it is defined.
from admin.endpoints import admin_save_room, admin_send_message, admin_set_availability  # noqa: E402
from health import health  # noqa: E402
from messages.answer_message import answer  # noqa: E402
from messages.send_message import send_message  # noqa: E402
from users.register import register  # noqa: E402

__all__ = [
    "health",
    "register",
    "send_message",
    "answer",
    "admin_save_room",
    "admin_set_availability",
    "admin_send_message",
]
