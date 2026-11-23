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


def test_validate_room():
    assert rooms.validate_room({"number": "101", "type": "Garden Room"}) == {
        "number": "101", "type": "Garden Room", "active": True}
    assert rooms.validate_room({"id": "a", "number": "A-1", "type": rooms.ROOM_TYPES[0], "active": False})["id"] == "a"
    for bad in [{"number": ""}, {"number": "x" * 11}, {"number": "1 2"}, {"number": 5},
                {"number": "1", "type": "Nope"}, {"number": "1", "type": rooms.ROOM_TYPES[0], "active": "yes"}]:
        bad.setdefault("type", rooms.ROOM_TYPES[0])
        with pytest.raises(AdminError) as e:
            rooms.validate_room(bad)
        assert e.value.status == 400


def test_save_room_creates(coll):
    coll.document.return_value.id = "new1"
    out = rooms.save_room({"number": "101", "type": "Garden Room"})
    assert out == {"room": {"id": "new1", "number": "101", "type": "Garden Room", "active": True}}
    saved = coll.document.return_value.set.call_args[0][0]
    assert saved["number"] == "101" and "createdAt" in saved and "updatedAt" in saved


def test_save_room_duplicate_number_conflicts(coll):
    coll.where.return_value.stream.return_value = [doc("other")]
    with pytest.raises(AdminError) as e:
        rooms.save_room({"number": "101", "type": "Garden Room"})
    assert (e.value.status, e.value.message) == (409, "Room number already in use.")
    coll.document.return_value.get.return_value = doc("mine")
    coll.where.return_value.stream.return_value = [doc("mine")]
    assert rooms.save_room({"id": "mine", "number": "101", "type": "Garden Room"})["room"]["id"] == "mine"


def test_save_room_unknown_id_is_404(coll):
    coll.document.return_value.get.return_value = MagicMock(exists=False)
    with pytest.raises(AdminError) as e:
        rooms.save_room({"id": "ghost", "number": "101", "type": "Garden Room"})
    assert e.value.status == 404


def test_list_rooms_sorted_by_number(coll):
    coll.stream.return_value = [
        doc("b", number="202", type="Garden Room", active=True, createdAt=1),
        doc("a", number="101", type="Garden Room", active=False, createdAt=1)]
    out = rooms.list_rooms({})
    assert [r["number"] for r in out["rooms"]] == ["101", "202"]
    assert out["rooms"][0] == {"id": "a", "number": "101", "type": "Garden Room", "active": False}
