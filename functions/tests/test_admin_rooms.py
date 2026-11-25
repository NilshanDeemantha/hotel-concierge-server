from unittest.mock import MagicMock, patch

import pytest

from admin import rooms
from admin.guard import AdminError


def doc(id, **data):
    d = MagicMock(id=id)
    d.to_dict.return_value = data
    d.exists = True
    return d


@pytest.fixture
def coll():
    with patch("admin.rooms.firestore.client") as client:
        c = client.return_value.collection.return_value
        c.where.return_value.stream.return_value = []
        yield c


def photos(room_id="r1", n=5):
    return [f"{rooms.PHOTO_PREFIX}{room_id}%2F{i}.jpg?alt=media&token=t" for i in range(n)]


ROOM = {"id": "r1", "number": "101", "name": "Palm Room", "type": "Garden Room", "price": 15000, "photos": photos()}
SAVED = {**ROOM, "amenities": [], "notice": "", "active": True}


def test_validate_room():
    assert rooms.validate_room(ROOM) == SAVED
    full = rooms.validate_room({**ROOM, "number": "A-1", "amenities": ["Ceiling fan", "Wardrobe"],
                                "notice": " No smoking ", "active": False})
    assert (full["amenities"], full["notice"], full["active"]) == (["Ceiling fan", "Wardrobe"], "No smoking", False)
    assert len(rooms.validate_room({**ROOM, "photos": photos(n=10)})["photos"]) == 10
    bad_cases = [
        {"id": None}, {"id": ""}, {"id": "a/b"}, {"id": "x" * 41},
        {"number": ""}, {"number": "x" * 11}, {"number": "1 2"}, {"number": 5},
        {"type": "Nope"}, {"active": "yes"}, {"name": " "}, {"name": "x" * 61}, {"name": 5},
        {"amenities": ["Jacuzzi"]}, {"amenities": ["Wardrobe", "Wardrobe"]}, {"amenities": "Wardrobe"},
        {"amenities": ["Non-smoking", "Smoking allowed"]},
        {"price": 0}, {"price": -5}, {"price": 99.5}, {"price": "100"}, {"price": True}, {"price": rooms.MAX_PRICE + 1},
        {"photos": photos(n=4)}, {"photos": photos(n=11)}, {"photos": photos() * 2}, {"photos": None},
        {"photos": [*photos(n=4), "https://evil.example/x.jpg"]}, {"photos": [*photos(n=4), 5]},
        {"photos": photos("other")},  # another room's folder
        {"notice": 5}, {"notice": "x" * 301},
    ]
    for bad in bad_cases:
        with pytest.raises(AdminError) as e:
            rooms.validate_room({**ROOM, **bad})
        assert e.value.status == 400, bad


def test_save_room_creates_with_the_given_id(coll):
    coll.document.return_value.get.return_value = MagicMock(exists=False)
    out = rooms.save_room(ROOM)
    coll.document.assert_called_with("r1")
    assert out == {"room": SAVED}
    saved = coll.document.return_value.set.call_args[0][0]
    assert saved["number"] == "101" and "createdAt" in saved and "updatedAt" in saved


def test_save_room_updates_an_existing_room(coll):
    coll.document.return_value.get.return_value = doc("r1")
    rooms.save_room(ROOM)
    saved = coll.document.return_value.set.call_args[0][0]
    assert "createdAt" not in saved and "updatedAt" in saved


def test_save_room_duplicate_number_conflicts(coll):
    coll.where.return_value.stream.return_value = [doc("other")]
    with pytest.raises(AdminError) as e:
        rooms.save_room(ROOM)
    assert (e.value.status, e.value.message) == (409, "Room number already in use.")
    coll.where.return_value.stream.return_value = [doc("r1")]  # the room itself is not a duplicate
    coll.document.return_value.get.return_value = doc("r1")
    assert rooms.save_room(ROOM)["room"]["id"] == "r1"
