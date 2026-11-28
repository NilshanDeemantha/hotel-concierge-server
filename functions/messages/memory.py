"""What the guest told Xenia that helps during this stay or the next one, a short list of notes per guest.

Xenia decides what is worth saving (the remember_guest_note tool); this code only sets the limits: short notes, few of them,
no look-alikes of card numbers or passwords, and they expire. The notes go back into the prompt on every reply, labelled as
what the guest said, so the real bookings (stay.py) always win over them.
"""

import re
from datetime import datetime, timedelta, timezone

from firebase_admin import firestore

from messages.dining_request import now

MAX_NOTES = 10
MAX_NOTE_CHARS = 120
KEEP = timedelta(days=365)
SECRET = re.compile(r"password|passcode|\bpin\b|cvv|cvc|card number", re.I)


def _fresh(notes: list[dict], at: datetime) -> list[dict]:
    return [n for n in notes if datetime.fromisoformat(n["at"]) > at - KEEP]


def _read(db, uid: str, at: datetime) -> list[dict]:
    snap = db.collection("guestMemory").document(uid).get()
    return _fresh((snap.to_dict() or {}).get("notes", []), at) if snap.exists else []


def remember(db, uid: str, note: str, at: datetime | None = None) -> dict:
    """Saves one note. Returns {"saved": True} or {"saved": False, "reason": ...}; the caller never shows the reason to the guest."""
    at = at or now()
    text = " ".join((note or "").split())[:MAX_NOTE_CHARS]
    if not text:
        return {"saved": False, "reason": "empty"}
    if SECRET.search(text) or len(re.sub(r"\D", "", text)) >= 8:
        return {"saved": False, "reason": "looks like a secret or a card or phone number; never save those"}
    notes = _read(db, uid, at)
    if any(n["text"].lower() == text.lower() for n in notes):
        return {"saved": True, "duplicate": True}
    notes = [*notes, {"text": text, "at": at.astimezone(timezone.utc).isoformat()}][-MAX_NOTES:]  # the oldest goes first
    # shortcut: read then write, so two notes in the same instant could overwrite each other; upgrade to a transaction if that ever matters.
    db.collection("guestMemory").document(uid).set({"uid": uid, "notes": notes, "updatedAt": firestore.SERVER_TIMESTAMP})
    return {"saved": True}


def context(uid: str, db=None, at: datetime | None = None) -> str:
    """The guest's notes as prompt lines, or "". Never raises: Xenia can answer without them."""
    try:
        notes = _read(db or firestore.client(), uid, at or now())
    except Exception:
        print("memory.context: could not read the notes")
        return ""
    if not notes:
        return ""
    return "What the guest has told us (their own words, not checked; the bookings above are always right if they differ):\n" + "\n".join(f"- {n['text']}" for n in notes)
