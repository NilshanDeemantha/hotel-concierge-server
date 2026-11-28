"""Checks a dining request against the menu before it is accepted. The model never decides what is allowed.

The rules: an order is for ONE meal (breakfast, lunch or dinner), every dish must be on that meal's menu and available,
and there is at most one dish of each course (one starter, one main course, one side, one dessert, one drink).
Dishes are in the `menuItems` collection (scripts/seed_menu.py). Everything raised here is words for the guest.
"""

MEALS = ("breakfast", "lunch", "dinner")
# Course -> how to say it to the guest. The order is the order of a meal. A guest gets one dish of each.
COURSES = {"starter": "starter", "main": "main course", "side": "side", "dessert": "dessert", "drink": "drink"}
MAX_ITEMS = 40  # dishes a request may mention before we stop reading it
MAX_MENU = 300  # dishes read from the menu


class DiningError(Exception):
    """Something the guest can be told in words."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _key(text: str) -> str:
    return " ".join(str(text).lower().split())


def validate_order(db, meal: str, dishes: list) -> dict:
    """The order if it follows the rules: {"meal", "items": [{id, name, course, priceLKR}], "totalLKR"}.
    `dishes` are dish names (or ids) as the guest and the menu give them. Raises DiningError."""
    meal = _key(meal or "")
    if meal not in MEALS:
        raise DiningError("Please tell me which meal this is for: breakfast, lunch or dinner.")
    asked = [str(d).strip() for d in (dishes or [])[:MAX_ITEMS] if isinstance(d, (str, int)) and str(d).strip()]
    if not asked:
        raise DiningError("Tell me which dishes you would like.")
    menu = [(d.id, d.to_dict() or {}) for d in db.collection("menuItems").limit(MAX_MENU).stream()]

    chosen: list[dict] = []
    for want in asked:
        found = [(i, m) for i, m in menu if want == i or _key(m.get("name", "")) == _key(want)]
        here = [(i, m) for i, m in found if m.get("meal") == meal]
        if not here and found:
            other = found[0][1]["meal"]
            raise DiningError(f"{found[0][1]['name']} is on the {other} menu, not the {meal} menu. An order is for one meal only.")
        if not here:
            raise DiningError(f"I could not find \"{want}\" on the {meal} menu. Please choose a dish from the menu.")
        dish_id, dish = here[0]
        if dish.get("available") is not True:
            raise DiningError(f"{dish['name']} is not available right now. Would you like to choose something else?")
        if any(c["id"] == dish_id for c in chosen):
            continue  # the same dish named twice is still one dish
        chosen.append({"id": dish_id, "name": dish["name"], "course": dish.get("course", ""), "priceLKR": dish["priceLKR"]})

    for course, label in COURSES.items():
        same = [c["name"] for c in chosen if c["course"] == course]
        if len(same) > 1:
            raise DiningError(f"You can order only one {label} at a time, but you chose {' and '.join(same)}. Which one would you like?")
    chosen.sort(key=lambda c: list(COURSES).index(c["course"]) if c["course"] in COURSES else len(COURSES))
    return {"meal": meal, "items": chosen, "totalLKR": sum(c["priceLKR"] for c in chosen)}
