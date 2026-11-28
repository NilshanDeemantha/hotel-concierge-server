"""Hand-over of a conversation to staff when Xenia cannot give the guest what they ask for.

Xenia calls escalate_to_staff; the conversation is then marked `needsAdmin` and she stays quiet in it (answer_message
skips it) until an admin replies or hands it back. The dashboard counts these conversations and lists them first.
The guest is never told about any of this: Xenia only says she will find out and come back to them in the chat.
"""

from firebase_admin import firestore

MAX_REASON_CHARS = 200


def escalate(db, conversation_id: str, reason: str) -> dict:
    """Marks the conversation as waiting for an admin. The first reason and time are kept if it is already waiting."""
    ref = db.collection("conversations").document(conversation_id)
    snap = ref.get()
    if snap.exists and (snap.to_dict() or {}).get("needsAdmin"):
        return {"handedOver": True}
    ref.update({
        "needsAdmin": True,
        "escalationReason": " ".join((reason or "").split())[:MAX_REASON_CHARS] or "Xenia could not answer",
        "escalatedAt": firestore.SERVER_TIMESTAMP,
    })
    return {"handedOver": True}


def is_waiting(db, conversation_id: str) -> bool:
    snap = db.collection("conversations").document(conversation_id).get()
    return bool(snap.exists and (snap.to_dict() or {}).get("needsAdmin"))


# What an admin's reply or hand-back writes to the conversation: Xenia takes over again.
HANDED_BACK = {"needsAdmin": False, "escalationReason": None, "escalatedAt": None}
