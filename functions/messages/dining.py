"""Checks a dining request against the menu before it is accepted. The model never decides what is allowed.

The rules: an order is for ONE meal (breakfast, lunch or dinner), every dish must be on that meal's menu and available,
and there is at most one dish of each course (one starter, one main course, one side, one dessert, one drink).
Dishes are in the `menuItems` collection (scripts/seed_menu.py). Everything raised here is words for the guest.
"""

import difflib
import re
import unicodedata

MEALS = ("breakfast", "lunch", "dinner")
# Course -> how to say it to the guest. The order is the order of a meal. A guest gets one dish of each.
COURSES = {"starter": "starter", "main": "main course", "side": "side", "dessert": "dessert", "drink": "drink"}
MAX_ITEMS = 40  # dishes a request may mention before we stop reading it
MAX_MENU = 300  # dishes read from the menu
MAX_NOTE_CHARS = 120  # a guest's special request for one dish (for example "less sugar")


class DiningError(Exception):
    """Something the guest can be told in words."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


FUZZY_MIN = 0.8  # how alike two names must be (0 to 1) for a typo to be forgiven
FUZZY_GAP = 0.05  # a close match counts only if the next best is clearly worse: otherwise the guest is asked


def _key(text: str) -> str:
    """A name as compared: lower case, no accents, no punctuation, single spaces ("Crème brûlée" -> "creme brulee")."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.lower()).split())


def _note(text) -> str:
    """A special request as saved: one line of plain text, cut to MAX_NOTE_CHARS. It is only ever shown to staff."""
    return " ".join("".join(c for c in str(text or "") if c.isprintable() or c.isspace()).split())[:MAX_NOTE_CHARS]


def _find(want: str, pool: list) -> tuple:
    """(the dish as (id, item) or None, names to ask about). Exact first, ignoring case and punctuation; then the one
    dish the words are clearly a typo of. Two dishes that are both close are not guessed between."""
    key = _key(want)
    for i, m in pool:
        if want == i or _key(m.get("name", "")) == key:
            return (i, m), []
    scored = sorted(((difflib.SequenceMatcher(None, key, _key(m.get("name", ""))).ratio(), i, m) for i, m in pool),
                    key=lambda s: -s[0])
    close = [s for s in scored if s[0] >= FUZZY_MIN]
    if not close:
        return None, []
    if len(close) > 1 and close[0][0] - close[1][0] < FUZZY_GAP:
        return None, [close[0][2]["name"], close[1][2]["name"]]
    return (close[0][1], close[0][2]), []


def validate_order(db, meal: str, dishes: list, special_requests: list | None = None) -> dict:
    """The order if it follows the rules: {"meal", "items": [{id, name, course, priceLKR, specialRequest}], "totalLKR"}.
    `dishes` are dish names (or ids) as the guest and the menu give them. `special_requests`, if given, has one entry per
    dish in the same order ("" for a dish with none): what the guest asked for that dish, like "less sugar". Raises DiningError."""
    meal = _key(meal or "")
    if meal not in MEALS:
        raise DiningError("Please tell me which meal this is for: breakfast, lunch or dinner.")
    dishes, notes = list(dishes or []), list(special_requests or [])
    if notes and len(notes) != len(dishes):
        raise DiningError("Please give one special request for each dish, in the same order as the dishes, and leave it empty for a dish with none.")
    asked = [(str(d).strip(), _note(notes[i]) if notes else "") for i, d in enumerate(dishes[:MAX_ITEMS])
             if isinstance(d, (str, int)) and str(d).strip()]
    if not asked:
        raise DiningError("Tell me which dishes you would like.")
    menu = [(d.id, d.to_dict() or {}) for d in db.collection("menuItems").limit(MAX_MENU).stream()]

    chosen: list[dict] = []
    for want, note in asked:
        here, unsure = _find(want, [(i, m) for i, m in menu if m.get("meal") == meal])
        if unsure:
            raise DiningError(f"I am not sure which dish you mean by \"{want}\": {unsure[0]} or {unsure[1]}?")
        if not here:
            elsewhere, _ = _find(want, [(i, m) for i, m in menu if m.get("meal") != meal])
            if elsewhere:
                raise DiningError(f"{elsewhere[1]['name']} is on the {elsewhere[1]['meal']} menu, not the {meal} menu. An order is for one meal only.")
            raise DiningError(f"I could not find \"{want}\" on the {meal} menu. Please choose a dish from the menu.")
        dish_id, dish = here
        if dish.get("available") is not True:
            raise DiningError(f"{dish['name']} is not available right now. Would you like to choose something else?")
        again = next((c for c in chosen if c["id"] == dish_id), None)
        if again:
            again["specialRequest"] = again["specialRequest"] or note  # the same dish named twice is still one dish
            continue
        chosen.append({"id": dish_id, "name": dish["name"], "course": dish.get("course", ""), "priceLKR": dish["priceLKR"], "specialRequest": note})

    for course, label in COURSES.items():
        same = [c["name"] for c in chosen if c["course"] == course]
        if len(same) > 1:
            raise DiningError(f"You can order only one {label} at a time, but you chose {' and '.join(same)}. Which one would you like?")
    chosen.sort(key=lambda c: list(COURSES).index(c["course"]) if c["course"] in COURSES else len(COURSES))
    return {"meal": meal, "items": chosen, "totalLKR": sum(c["priceLKR"] for c in chosen)}
