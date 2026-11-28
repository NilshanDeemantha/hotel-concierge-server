import json
from pathlib import Path

import pytest

from fakes import Db, Store
from messages import dining
from messages.dining import DiningError, validate_order

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import seed_menu  # noqa: E402

ITEMS = json.loads((Path(seed_menu.__file__).with_name("menu_items.json")).read_text())


@pytest.fixture
def db():
    store = Store()
    for item in ITEMS:
        store.put(f"menuItems/{seed_menu.doc_id(item)}", {**item, "available": True})
    return Db(store)


def refused(db, meal, dishes) -> str:
    with pytest.raises(DiningError) as e:
        validate_order(db, meal, dishes)
    return e.value.message


def test_one_dish_of_each_course_is_accepted_and_totalled_in_meal_order(db):
    order = validate_order(db, "Dinner", ["Soft drinks", "Beef steak", "Crab cakes", "Cheesecake"])  # any order, any case
    assert order["meal"] == "dinner"
    assert [i["course"] for i in order["items"]] == ["starter", "main", "dessert", "drink"]
    assert order["totalLKR"] == 2900 + 4800 + 1400 + 500 and order["items"][0]["id"] == "dinner-crab-cakes"


def test_a_shorter_order_and_the_dish_id_work(db):
    assert validate_order(db, "lunch", ["Fish and chips"])["totalLKR"] == 3200
    assert validate_order(db, "lunch", ["lunch-fish-and-chips", "  FISH AND CHIPS "])["items"][0]["name"] == "Fish and chips"  # same dish twice is one


@pytest.mark.parametrize("dishes, words", [
    (["Crab cakes", "Prawn cocktail"], "one starter"),
    (["Beef steak", "Lamb chops"], "one main course"),   # a Sri Lankan and a western main are both mains
    (["Jaffna crab curry", "Lamb chops"], "one main course"),
    (["Cheesecake", "Crème brûlée"], "one dessert"),
    (["Soft drinks", "Fresh juice"], "one drink"),
    (["Chips or mashed potato", "Garden salad"], "one side"),
])
def test_two_dishes_of_the_same_course_are_refused_naming_both(db, dishes, words):
    message = refused(db, "dinner", dishes)
    assert words in message and all(d in message for d in dishes)


def test_a_dish_of_another_meal_is_refused_and_says_where_it_is(db):
    message = refused(db, "dinner", ["Beef steak", "Fish cutlets (4)"])   # a lunch starter in a dinner order
    assert "Fish cutlets (4) is on the lunch menu, not the dinner menu" in message and "one meal only" in message
    assert "breakfast menu, not the lunch" in refused(db, "lunch", ["Full English"])


def test_a_dish_that_is_on_two_menus_is_read_from_the_meal_asked(db):
    assert validate_order(db, "breakfast", ["Fresh juice"])["items"][0]["id"] == "breakfast-fresh-juice"
    assert validate_order(db, "dinner", ["Fresh juice"])["items"][0]["id"] == "dinner-fresh-juice"


def test_unknown_dishes_unavailable_dishes_and_empty_orders_are_refused(db):
    assert "could not find" in refused(db, "dinner", ["Beef Wellington"])
    assert "Tell me which dishes" in refused(db, "dinner", [])
    assert "Tell me which dishes" in refused(db, "dinner", ["", "  "])
    db.store.docs["menuItems/dinner-beef-steak"]["available"] = False
    assert "Beef steak is not available" in refused(db, "dinner", ["Beef steak"])
    db.store.docs["menuItems/dinner-cheesecake"].pop("available")  # a dish with no flag is not offered
    assert "not available" in refused(db, "dinner", ["Cheesecake"])


def test_the_meal_must_be_breakfast_lunch_or_dinner(db):
    for meal in ("", "brunch", None):
        assert "which meal" in refused(db, meal, ["Beef steak"])


def test_a_request_cannot_name_more_dishes_than_there_are_courses_and_one_per_course_holds(db):
    five = ["Crab cakes", "Beef steak", "Chips or mashed potato", "Cheesecake", "Soft drinks"]
    assert len(validate_order(db, "dinner", five)["items"]) == len(dining.COURSES)
    assert "one main course" in refused(db, "dinner", five + ["Roast chicken"])
