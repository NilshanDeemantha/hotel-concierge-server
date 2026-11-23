from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from admin import availability as av
from admin.guard import AdminError


def raises(status, fn, *args):
    with pytest.raises(AdminError) as e:
        fn(*args)
    assert e.value.status == status
    return e.value


def dec(*days):
    return [date(2026, 12, d) for d in days]


def test_parse_nights():
    assert av.parse_nights("2026-12-20", "2026-12-23") == dec(20, 21, 22)
    assert av.parse_nights("2026-12-30", "2027-01-02") == dec(30, 31) + [date(2027, 1, 1)]
    for start, end in [("2026-02-30", "2026-03-02"), ("20-12-2026", "2026-12-23"), ("2026-12-20", "2026-12-20"),
                       ("2026-12-23", "2026-12-20"), ("2026-01-01", "2026-03-03"), (None, "2026-12-20")]:
        raises(400, av.parse_nights, start, end)
    assert len(av.parse_nights("2026-01-01", "2026-03-02")) == 60


def test_plan_changes_books_across_months():
    nights = av.parse_nights("2026-12-30", "2027-01-02")
    assert av.plan_changes({}, "r1", nights, "booked", "VIP") == {
        "r1_2026-12": {"30": {"status": "booked", "note": "VIP"}, "31": {"status": "booked", "note": "VIP"}},
        "r1_2027-01": {"01": {"status": "booked", "note": "VIP"}}}
    assert av.plan_changes({}, "r1", dec(5), "blocked", None) == {"r1_2026-12": {"05": {"status": "blocked"}}}


def test_plan_changes_conflict_is_all_or_nothing():
    existing = {"r1_2026-12": {"21": {"status": "booked"}}}
    e = raises(409, av.plan_changes, existing, "r1", dec(20, 21, 22), "booked", None)
    assert "2026-12-21" in e.message


def test_plan_changes_free_only_deletes_existing():
    existing = {"r1_2026-12": {"21": {"status": "booked"}}}
    assert av.plan_changes(existing, "r1", dec(20, 21, 22), "free", None) == {"r1_2026-12": {"21": None}}
    assert av.plan_changes({}, "r1", dec(20, 21, 22), "free", None) == {}


def test_plan_changes_blocked_over_booked_conflicts():
    existing = {"r1_2026-12": {"22": {"status": "booked"}}}
    assert "2026-12-22" in raises(409, av.plan_changes, existing, "r1", dec(20, 21, 22), "blocked", None).message


def snap(exists=True, **data):
    s = MagicMock(exists=exists)
    s.to_dict.return_value = data
    return s


@pytest.fixture
def db():
    with patch("admin.availability.firestore.client") as client:
        c = client.return_value
        cols = {"rooms": MagicMock(), "availability": MagicMock()}
        c.collection.side_effect = cols.__getitem__
        cols["rooms"].document.return_value.get.return_value = snap(number="101", type="Garden Room", active=True)
        refs = {}

        def ref(id):
            if id not in refs:
                refs[id] = MagicMock(id=id)
                refs[id].get.return_value = snap(exists=False)
            return refs[id]
        cols["availability"].document.side_effect = ref
        tx = c.transaction.return_value
        tx._max_attempts, tx._read_only = 1, False
        yield MagicMock(cols=cols, refs=refs, tx=tx, ref=ref)


BODY = {"roomId": "r1", "from": "2026-12-30", "to": "2027-01-02", "status": "booked"}


def test_set_availability_unknown_room_is_404(db):
    db.cols["rooms"].document.return_value.get.return_value = snap(exists=False)
    raises(404, av.set_availability, BODY)
    db.tx.set.assert_not_called()


def test_set_availability_inactive_room_cannot_be_booked(db):
    db.cols["rooms"].document.return_value.get.return_value = snap(number="101", type="Garden Room", active=False)
    raises(409, av.set_availability, BODY)
    raises(409, av.set_availability, {**BODY, "status": "blocked"})
    db.tx.set.assert_not_called()
    db.ref("r1_2026-12").get.return_value = snap(roomId="r1", month="2026-12", days={"30": {"status": "booked"}})
    assert av.set_availability({**BODY, "status": "free"}) == {"nights": 3}
    db.tx.set.assert_called_once_with(db.ref("r1_2026-12"), {"roomId": "r1", "month": "2026-12",
                                                             "days": {"30": av.firestore.DELETE_FIELD}}, merge=True)


def test_set_availability_rejects_bad_status_and_long_note(db):
    raises(400, av.set_availability, {**BODY, "status": "maybe"})
    raises(400, av.set_availability, {**BODY, "note": "x" * 201})
    raises(400, av.set_availability, {**BODY, "note": 5})
    raises(400, av.set_availability, {**BODY, "to": "2026-12-30"})
    assert av.set_availability({**BODY, "note": "x" * 200}) == {"nights": 3}


def test_set_availability_writes_merge_per_month(db):
    assert av.set_availability(BODY) == {"nights": 3}
    for call in db.refs.values():
        call.get.assert_called_once_with(transaction=db.tx)
    booked = {"status": "booked"}
    assert sorted(db.tx.set.call_args_list, key=lambda c: c.args[0].id) == [
        ((db.ref("r1_2026-12"), {"roomId": "r1", "month": "2026-12", "days": {"30": booked, "31": booked}}), {"merge": True}),
        ((db.ref("r1_2027-01"), {"roomId": "r1", "month": "2027-01", "days": {"01": booked}}), {"merge": True})]


def test_set_availability_conflict_writes_nothing(db):
    db.ref("r1_2027-01").get.return_value = snap(roomId="r1", month="2027-01", days={"01": {"status": "blocked"}})
    assert "2027-01-01" in raises(409, av.set_availability, BODY).message
    db.tx.set.assert_not_called()

