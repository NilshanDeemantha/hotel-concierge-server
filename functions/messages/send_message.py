"""The send_message function."""

from firebase_admin import auth
from firebase_admin.exceptions import FirebaseError
from firebase_functions import https_fn

from common import POST_ONLY_CORS, json_response
from messages import chat

# 1000 characters can take 4 bytes each, plus the JSON around them.
MAX_BODY_BYTES = 4400


def _verified_uid(req: https_fn.Request) -> str:
    """The signed-in user's uid, from the Firebase ID token the site sends as a Bearer token."""
    header = req.headers.get("Authorization", "")
    try:
        if not header.startswith("Bearer "):
            raise ValueError("missing token")
        return auth.verify_id_token(header[len("Bearer "):])["uid"]
    except (ValueError, FirebaseError):
        raise chat.ChatError(401, "Please log in again.")


@https_fn.on_request(cors=POST_ONLY_CORS)
def send_message(req: https_fn.Request) -> https_fn.Response:
    """Saves the signed-in user's message in their conversation. Body: {text}."""
    if req.method != "POST":
        return json_response({"error": "Method not allowed"}, 405)
    if (req.content_length or 0) > MAX_BODY_BYTES:
        return json_response({"error": "Request too large"}, 413)
    try:
        uid = _verified_uid(req)
        text = chat.validate_text(req.get_json(silent=True))
        saved = chat.add_user_message(uid, text)
    except chat.ChatError as e:
        return json_response({"error": e.message}, e.status)
    except Exception:
        print("send_message failed unexpectedly")
        import traceback

        traceback.print_exc()
        return json_response({"error": "Something went wrong. Please try again."}, 500)
    return json_response(saved, 201)
