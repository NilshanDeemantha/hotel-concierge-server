"""Admin read-only view of guest conversations."""

from datetime import datetime, timezone

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from admin.guard import AdminError

_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


def _iso(ts):
    return ts.isoformat() if ts else None


def list_conversations(body: dict) -> dict:
    db = firestore.client()
    docs = list(db.collection("conversations").order_by("updatedAt", direction="DESCENDING").limit(100).stream())
    uids = list({d.to_dict()["uid"] for d in docs})
    refs = [db.collection("users").document(u) for u in uids]
    names = {s.id: (s.to_dict() or {}).get("name") for s in db.get_all(refs) if s.exists} if refs else {}
    out = []
    for d in docs:
        c = d.to_dict()
        out.append({"id": d.id, "uid": c["uid"], "name": names.get(c["uid"]) or "Unknown guest",
                    "messageCount": c.get("messageCount", 0), "updatedAt": _iso(c.get("updatedAt"))})
    return {"conversations": out}


def get_messages(body: dict) -> dict:
    cid = body.get("conversationId")
    if not isinstance(cid, str) or not cid:
        raise AdminError(400, "conversationId is required.")
    db = firestore.client()
    if not db.collection("conversations").document(cid).get().exists:
        raise AdminError(404, "Conversation not found.")
    docs = db.collection("messages").where(filter=FieldFilter("conversationId", "==", cid)).stream()
    # Sorted here, not in the query: ordering needs a composite index (conversationId+createdAt) that doesn't exist.
    msgs = sorted((d for d in docs), key=lambda d: d.to_dict().get("createdAt") or _FAR_FUTURE)[:500]
    return {"messages": [
        {"id": d.id, "role": d.to_dict()["role"], "content": d.to_dict()["content"],
         "createdAt": _iso(d.to_dict().get("createdAt"))} for d in msgs]}
