"""Chat storage: one conversation per user and the messages sent in it.

Collections are top level and every document carries the owner's uid; browsers never read
Firestore, so the server checks that uid on every request.
"""

from datetime import datetime, timedelta, timezone

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

MAX_MESSAGE_CHARS = 1000
# Set a Firestore TTL policy on `expireAt` for both collections to make this delete anything.
RETENTION = timedelta(days=30)


class ChatError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _expire_at() -> datetime:
    return datetime.now(timezone.utc) + RETENTION


def conversation_fields(uid: str) -> dict:
    return {
        "uid": uid,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
        "messageCount": 0,
        "expireAt": _expire_at(),
    }


def validate_text(data: object) -> str:
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ChatError(400, "Type a message first.")
    text = text.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        raise ChatError(400, f"Keep messages under {MAX_MESSAGE_CHARS} characters.")
    return text


def _conversation_ref(db, uid: str):
    """The user's conversation. Accounts made before chat existed get theirs on first use."""
    found = db.collection("conversations").where(filter=FieldFilter("uid", "==", uid)).limit(1).get()
    if found:
        return found[0].reference
    # shortcut: two first messages at the same instant could create two; fine for one-user demo traffic.
    ref = db.collection("conversations").document()
    ref.set(conversation_fields(uid))
    return ref


def add_user_message(uid: str, text: str) -> dict:
    """Saves the user's message as a new document. Returns its id and its conversation's id."""
    db = firestore.client()
    conversation = _conversation_ref(db, uid)
    message = db.collection("messages").document()
    batch = db.batch()
    batch.set(
        message,
        {
            "conversationId": conversation.id,
            "uid": uid,
            "role": "user",
            "content": text,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "expireAt": _expire_at(),
        },
    )
    # A chat that is still in use must not expire, so each message pushes the date forward.
    batch.update(
        conversation,
        {"messageCount": firestore.Increment(1), "updatedAt": firestore.SERVER_TIMESTAMP, "expireAt": _expire_at()},
    )
    batch.commit()
    return {"messageId": message.id, "conversationId": conversation.id}


def reply_id(message_id: str) -> str:
    """Xenia's answer to a message has a fixed id, so a repeated task cannot answer twice."""
    return f"{message_id}-reply"


def set_typing(conversation, typing: bool) -> None:
    conversation.update({"typing": typing})


def save_reply(message: dict, message_id: str, text: str) -> None:
    """Saves Xenia's answer to the user's message and clears her typing state, together."""
    db = firestore.client()
    conversation = db.collection("conversations").document(message["conversationId"])
    reply = db.collection("messages").document(reply_id(message_id))
    batch = db.batch()
    batch.create(
        reply,
        {
            "conversationId": message["conversationId"],
            "uid": message["uid"],
            "role": "assistant",
            "content": text,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "expireAt": _expire_at(),
        },
    )
    batch.update(
        conversation,
        {
            "typing": False,
            "messageCount": firestore.Increment(1),
            "updatedAt": firestore.SERVER_TIMESTAMP,
            "expireAt": _expire_at(),
        },
    )
    batch.commit()
