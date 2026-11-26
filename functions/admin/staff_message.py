"""Admin reply: saves a staff message in a guest's conversation (role "staff"), which the guest's chat shows live."""

from firebase_admin import firestore

from admin.guard import AdminError
from messages import chat


def send_staff_message(body: dict) -> dict:
    conversation_id = body.get("conversationId")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise AdminError(400, "Invalid conversation id.")
    try:
        text = chat.validate_text(body)
    except chat.ChatError as e:
        raise AdminError(e.status, e.message)

    db = firestore.client()
    conversation = db.collection("conversations").document(conversation_id)
    snap = conversation.get()
    if not snap.exists:
        raise AdminError(404, "Conversation not found.")
    message = db.collection("messages").document()
    batch = db.batch()
    batch.set(
        message,
        {
            "conversationId": conversation_id,
            "uid": snap.to_dict()["uid"],
            "role": "staff",
            "content": text,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "expireAt": chat._expire_at(),
        },
    )
    batch.update(
        conversation,
        {"messageCount": firestore.Increment(1), "updatedAt": firestore.SERVER_TIMESTAMP, "expireAt": chat._expire_at()},
    )
    batch.commit()
    return {"messageId": message.id}
