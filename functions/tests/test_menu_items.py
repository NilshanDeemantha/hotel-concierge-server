"""The seed data of the menuItems collection: what the PDF menu shows, one entry per dish."""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import seed_menu  # noqa: E402

ITEMS = json.loads((SCRIPTS / "menu_items.json").read_text())
LABELS = {"vegetarian", "vegan", "spicy", "seafood", "gluten-free", "dairy-free", "contains-nuts"}
FIELDS = {"meal", "category", "course", "name", "description", "priceLKR", "labels", "sortOrder"}
COURSES = {"starter", "main", "side", "dessert", "drink"}


def test_every_dish_has_exactly_the_menu_fields_and_sensible_values():
    assert len(ITEMS) == 74
    for d in ITEMS:
        assert set(d) == FIELDS, d["name"]
        assert d["meal"] in ("breakfast", "lunch", "dinner") and d["course"] in COURSES
        assert all(isinstance(d[k], str) and d[k].strip() for k in ("category", "name", "description"))
        assert isinstance(d["priceLKR"], int) and 0 < d["priceLKR"] < 100_000
        assert set(d["labels"]) <= LABELS and len(set(d["labels"])) == len(d["labels"])
        assert not ("vegan" in d["labels"] and "vegetarian" in d["labels"])  # vegan already means vegetarian


def test_ids_are_unique_and_the_order_follows_the_menu():
    ids = [seed_menu.doc_id(d) for d in ITEMS]
    assert len(set(ids)) == len(ids)
    assert [d["sortOrder"] for d in ITEMS] == list(range(len(ITEMS)))
    assert seed_menu.doc_id({"meal": "lunch", "name": "Fish and chips"}) == "lunch-fish-and-chips"
    assert seed_menu.doc_id({"meal": "dinner", "name": "Ploughman's platter"}) == "dinner-ploughman-s-platter"


def test_every_meal_has_dishes_and_no_dish_has_an_image_field():
    assert {d["meal"] for d in ITEMS} == {"breakfast", "lunch", "dinner"}
    assert not any("image" in k.lower() or "icon" in k.lower() for d in ITEMS for k in d)
