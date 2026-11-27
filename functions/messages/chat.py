"""Chat storage: one conversation per user and the messages sent in it.

Collections are top level and every document carries the owner's uid; browsers never read
Firestore, so the server checks that uid on every request.
"""

from datetime import datetime, timedelta, timezone

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

MAX_MESSAGE_CHARS = 1000
HISTORY_LIMIT = 12  # messages of the conversation given to the model
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


def recent_messages(conversation_id: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """The latest messages of a conversation, oldest first, so Xenia remembers dates and rooms from earlier."""
    db = firestore.client()
    try:
        found = (
            db.collection("messages")
            .where(filter=FieldFilter("conversationId", "==", conversation_id))
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .get()
        )
    except Exception:
        # The (conversationId, createdAt) index is deployed from firestore.indexes.json; until it is built Xenia
        # answers from the current message alone.
        print("recent_messages: query failed, is the index built?")
        return []
    return [{"role": d.get("role"), "content": d.get("content"), "images": d.to_dict().get("images", [])} for d in reversed(found)]


def set_payment_status(booking_id: str, status: str) -> None:
    """Marks every payment-link message of a booking as "paid" or "closed", so the chat (the guest's and the admin's)
    shows Payment done or Link expired instead of a link that no longer works. Never raises: the payment itself is already settled."""
    try:
        db = firestore.client()
        for d in db.collection("messages").where(filter=FieldFilter("paymentBookingId", "==", booking_id)).stream():
            d.reference.update({"payment.status": status})
    except Exception:
        print(f"could not mark the payment messages of booking {booking_id} as {status}")


def save_notice(conversation_id: str, uid: str, doc_id: str, text: str, **extra) -> None:
    """Saves a message from the server (a payment link, a receipt) in a conversation. Raises AlreadyExists if repeated."""
    db = firestore.client()
    batch = db.batch()
    batch.create(
        db.collection("messages").document(doc_id),
        {
            "conversationId": conversation_id,
            "uid": uid,
            "role": "assistant",
            "content": text,
            **extra,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "expireAt": _expire_at(),
        },
    )
    batch.update(
        db.collection("conversations").document(conversation_id),
        {"messageCount": firestore.Increment(1), "updatedAt": firestore.SERVER_TIMESTAMP, "expireAt": _expire_at()},
    )
    batch.commit()


def save_reply(message: dict, message_id: str, text: str, doc_id: str | None = None, images: list[str] | None = None) -> None:
    """Saves Xenia's answer to the user's message and clears her typing state, together.

    `doc_id` is fixed per answer, so a repeated task cannot save the same answer twice.
    """
    db = firestore.client()
    conversation = db.collection("conversations").document(message["conversationId"])
    reply = db.collection("messages").document(doc_id or reply_id(message_id))
    batch = db.batch()
    batch.create(
        reply,
        {
            "conversationId": message["conversationId"],
            "uid": message["uid"],
            "role": "assistant",
            "content": text,
            **({"images": images} if images else {}),
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
