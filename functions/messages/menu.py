"""The à la carte menu, sent to the guest as a PDF in the chat with a short message about preparation times.

The PDF lives in Firebase Storage under menus/ (uploaded by staff, not by this code). The message is written here,
not by the model, so the times and hours are exact. Staff change the file or the times below when the menu changes.
"""

from google.api_core.exceptions import AlreadyExists

from messages import chat

BUCKET = "thambili-bay-concierge.firebasestorage.app"
# Only a link under this prefix is ever sent to a guest as a file.
URL_PREFIX = f"https://firebasestorage.googleapis.com/v0/b/{BUCKET}/o/menus%2F"
MENU_PDF = {
    "url": URL_PREFIX + "thambili-bay-a-la-carte-menu.pdf?alt=media&token=dcb27fe5-3256-40e3-994c-7332d97b83ba",
    "name": "Thambili Bay - A la carte menu.pdf",
}

# When each meal is served and how long the kitchen needs once an order is in. Placeholders: set the real times.
MEALS = {
    "breakfast": {"served": "7:00 am to 10:30 am", "prep": "20 to 30 minutes"},
    "lunch": {"served": "12:00 pm to 3:30 pm", "prep": "30 to 40 minutes"},
    "dinner": {"served": "6:30 pm to 10:00 pm", "prep": "35 to 45 minutes"},
}


def menu_text(meal: str = "") -> str:
    """The short message sent with the PDF: for one meal if the guest asked about one, otherwise for all three."""
    key = (meal or "").strip().lower()
    shown = [key] if key in MEALS else list(MEALS)
    lines = "; ".join(f"{m} is served {MEALS[m]['served']} and takes about {MEALS[m]['prep']} to prepare" for m in shown)
    return (
        "Here is our à la carte menu as a PDF, with every dish, a short description and its price in LKR. "
        f"{lines[0].upper() + lines[1:]}. Ask me about any dish."
    )


def send(message: dict, message_id: str, meal: str = "") -> None:
    """Saves the menu message in the guest's conversation. The id is fixed per question, so a repeat cannot send it twice."""
    try:
        chat.save_notice(message["conversationId"], message["uid"], f"{message_id}-menu", menu_text(meal), document=MENU_PDF)
    except AlreadyExists:
        pass
