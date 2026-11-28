import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fakes import Db, Store
from messages import answer_message as task, booking, dining_request as dr, tools
from messages.dining import DiningError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import seed_menu  # noqa: E402

ITEMS = json.loads((Path(seed_menu.__file__).with_name("menu_items.json")).read_text())
DURING = datetime(2026, 12, 21, 19, 0, tzinfo=booking.TZ)  # the stay below runs 20 Dec 14:00 to 23 Dec 11:00
STAY = {"uid": "u1", "status": "confirmed", "reference": "TB-AAAAAA", "roomId": "r1", "roomNumber": "202",
        "checkIn": "2026-12-20", "checkOut": "2026-12-23", "checkInTime": "14:00", "checkOutTime": "11:00"}
ORDER = dict(meal="dinner", dishes=["Crab cakes", "Beef steak", "Soft drinks"], service="room")


@pytest.fixture
def store():
    s = Store()
    for item in ITEMS:
        s.put(f"menuItems/{seed_menu.doc_id(item)}", {**item, "available": True})
    s.put("bookings/b1", STAY)
    s.put("users/u1", {"name": "Amara Perera"})
    return s


def make(store, at=DURING, message_id="m1", **over):
    with patch.object(dr, "now", return_value=at):
        return dr.create(Db(store), uid="u1", conversation_id="c1", message_id=message_id, **{**ORDER, **over})


def refused(store, at=DURING, **over) -> str:
    with pytest.raises(DiningError) as e:
        make(store, at, **over)
    return e.value.message


def test_a_request_saves_the_dishes_the_guest_the_room_and_the_total(store):
    r = make(store)
    saved = store.docs["diningRequests/m1-dining"]
    assert saved["uid"] == "u1" and saved["guestName"] == "Amara Perera" and saved["conversationId"] == "c1"
    assert saved["roomId"] == "r1" and saved["roomNumber"] == "202" and saved["bookingId"] == "b1" and saved["bookingReference"] == "TB-AAAAAA"
    assert saved["meal"] == "dinner" and saved["service"] == "room" and saved["status"] == "requested"
    assert [i["name"] for i in saved["items"]] == ["Crab cakes", "Beef steak", "Soft drinks"]
    assert saved["totalLKR"] == 2900 + 4800 + 500 == r["totalLKR"] and saved["reference"].startswith("DR-")
    assert "createdAt" in saved and "expireAt" in saved


def test_the_message_names_the_dishes_total_and_where_the_food_goes(store):
    room = dr.request_text(make(store))
    assert "Crab cakes (LKR 2,900)" in room and "Total LKR 8,200" in room and "brought to room 202" in room
    restaurant = dr.request_text(make(store, message_id="m2", service="Restaurant"))
    assert "served at the restaurant" in restaurant and "room 202" not in restaurant


def test_the_guest_must_say_room_or_restaurant(store):
    for service in ("", "garden", None):
        assert "room or whether you will come to the restaurant" in refused(store, service=service)
    assert not any(p.startswith("diningRequests/") for p in store.docs)


def test_the_guest_must_be_in_their_booking_time_for_either_service(store):
    for service in ("room", "restaurant"):
        assert "stay" in refused(store, service=service, at=datetime(2026, 12, 20, 13, 59, tzinfo=booking.TZ))  # before check-in
        assert "stay" in refused(store, service=service, at=datetime(2026, 12, 23, 11, 1, tzinfo=booking.TZ))   # after check-out
        assert "stay" in refused(store, service=service, at=datetime(2027, 3, 1, 12, 0, tzinfo=booking.TZ))
    assert make(store, at=datetime(2026, 12, 20, 14, 0, tzinfo=booking.TZ), message_id="a")["status"] == "requested"   # the very start
    assert make(store, at=datetime(2026, 12, 23, 11, 0, tzinfo=booking.TZ), message_id="b", service="restaurant")["status"] == "requested"  # the very end


@pytest.mark.parametrize("change", [{"status": "payment_pending"}, {"status": "cancelled"}, {"uid": "someone-else"}])
def test_only_the_guests_own_confirmed_booking_counts(store, change):
    store.docs["bookings/b1"] = {**STAY, **change}
    assert "stay" in refused(store)


def test_the_booking_that_covers_now_is_used_when_the_guest_has_several(store):
    store.put("bookings/b0", {**STAY, "roomId": "r9", "roomNumber": "101", "reference": "TB-OLD", "checkIn": "2026-10-01", "checkOut": "2026-10-03"})
    assert make(store)["roomNumber"] == "202"


def test_the_dishes_are_checked_before_anything_is_saved(store):
    assert "only one starter" in refused(store, dishes=["Crab cakes", "Prawn cocktail"])
    assert "lunch menu, not the dinner menu" in refused(store, dishes=["Crab cakes", "Fish cutlets (4)"])
    assert not any(p.startswith("diningRequests/") for p in store.docs)


def test_a_repeated_task_does_not_order_twice(store):
    first = make(store)
    again = make(store)
    assert again["alreadyRequested"] is True and again["reference"] == first["reference"]
    assert sum(p.startswith("diningRequests/") for p in store.docs) == 1


def test_a_guest_cannot_have_more_than_three_requests_waiting(store):
    for i in range(3):
        make(store, message_id=f"m{i}")
    assert "several dining requests waiting" in refused(store, message_id="m9")
    store.docs["diningRequests/m0-dining"]["status"] = "served"
    assert make(store, message_id="m9")["status"] == "requested"


def test_the_tools_check_then_place_only_after_the_guests_yes_and_never_raise(store):
    made: list[dict] = []
    with patch.object(tools.firestore, "client", return_value=Db(store)), patch.object(dr, "now", return_value=DURING):
        t = {f.__name__: f for f in tools.make_tools("u1", "c1", "m1", [], [], dining_made=made)}
        assert t["check_dining_order"]("dinner", ORDER["dishes"])["totalLKR"] == 8200
        assert "only one starter" in t["check_dining_order"]("dinner", ["Crab cakes", "Prawn cocktail"])["error"]
        assert "error" in t["place_dining_request"]("dinner", ORDER["dishes"], "room", False) and not made
        assert "error" in t["place_dining_request"]("dinner", ORDER["dishes"], "", True) and not made
        ok = t["place_dining_request"]("dinner", ORDER["dishes"], "room", True)
        assert ok["requested"] is True and ok["totalLKR"] == 8200 and len(made) == 1
        t["place_dining_request"]("dinner", ORDER["dishes"], "room", True)  # the model calling it twice
        assert len(made) == 1
        names = [d["name"] for d in t["list_menu_dishes"]("lunch")["dishes"]]
        assert "Fish and chips" in names and "Crab cakes" not in names
        assert "error" in t["list_menu_dishes"]("brunch")
    store.docs["bookings/b1"] = {**STAY, "status": "cancelled"}
    with patch.object(tools.firestore, "client", return_value=Db(store)), patch.object(dr, "now", return_value=DURING):
        assert "stay" in {f.__name__: f for f in tools.make_tools("u1", "c1", "m2", [], [])}["check_dining_order"]("dinner", ORDER["dishes"])["error"]


@patch("messages.answer_message.dining_request")
@patch("messages.answer_message.notices", new=MagicMock())
@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.firestore")
def test_answer_sends_the_request_message_after_the_reply(firestore, reply_mod, chat_mod, dr_mod):
    db = firestore.client()
    message = {"conversationId": "c1", "uid": "u1", "content": "yes please"}
    db.collection().document().get().to_dict.return_value = message
    db.collection().document().get().exists = False
    reply_mod.FALLBACK, reply_mod.BOOKED_FALLBACK = "sorry", "booked"
    request = {"reference": "DR-1"}
    reply_mod.generate_reply.side_effect = lambda m, mid, booked, images, resend=None, cancelled=None, dining_made=None: (dining_made.append(request), "Placing it.")[1]
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    dr_mod.send.assert_called_once_with(message, "m1", request)
