"""Hands a conversation back to Xenia without a reply (the admin saw it and nothing needs saying)."""

from firebase_admin import firestore

from admin.guard import AdminError
from messages import escalation


def hand_back(body: dict) -> dict:
    conversation_id = body.get("conversationId")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise AdminError(400, "Invalid conversation id.")
    ref = firestore.client().collection("conversations").document(conversation_id)
    if not ref.get().exists:
        raise AdminError(404, "Conversation not found.")
    ref.update(escalation.HANDED_BACK)
    return {"handedBack": True}
