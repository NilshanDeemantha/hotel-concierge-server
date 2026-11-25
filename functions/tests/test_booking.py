"""Xenia's room tools and booking, run against a small in-memory Firestore that checks transactions the way
Firestore does: a commit is refused (Aborted) if a document the transaction read has changed since."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import Aborted

from messages import booking as bk
from messages import tools


class Snap:
    def __init__(self, id, data):
        self.id, self._data, self.exists = id, data, data is not None

    def to_dict(self):
        return None if self._data is None else {k: (dict(v) if isinstance(v, dict) else v) for k, v in self._data.items()}


class Store:
    def __init__(self):
        self.docs, self.versions, self.before_commit = {}, {}, None

    def put(self, path, data):
        self.docs[path] = data
        self.versions[path] = self.versions.get(path, 0) + 1


class Ref:
    def __init__(self, store, path):
        self.store, self.path, self.id = store, path, path.split("/")[1]

    def get(self, transaction=None):
        if transaction is not None:
            transaction.reads[self.path] = self.store.versions.get(self.path, 0)
        return Snap(self.id, self.store.docs.get(self.path))


class Query:
    def __init__(self, store, name, rows=None):
        self.store, self.name, self.rows = store, name, rows

    def _all(self):
        if self.rows is not None:
            return self.rows
        return [Snap(p.split("/")[1], d) for p, d in self.store.docs.items() if p.startswith(self.name + "/")]

    def where(self, filter):
        keep = {"==": lambda a, b: a == b, "in": lambda a, b: a in b}[filter.op_string]
        return Query(self.store, self.name, [s for s in self._all() if keep(s._data.get(filter.field_path), filter.value)])

    def limit(self, n):
        return self

    def stream(self):
        return iter(self._all())


class Collection(Query):
    def document(self, id):
        return Ref(self.store, f"{self.name}/{id}")


class Tx:
    def __init__(self, store):
        self.store, self.reads, self.writes = store, {}, []

    def set(self, ref, data, merge=False):
        self.writes.append((ref.path, data, merge))

    def commit(self):
        if self.store.before_commit:
            hook, self.store.before_commit = self.store.before_commit, None
            hook()  # another guest's booking lands while this one is still in flight
        if any(self.store.versions.get(p, 0) != v for p, v in self.reads.items()):
            raise Aborted("contention")
        for path, data, merge in self.writes:
            current = dict(self.store.docs.get(path) or {})
            if merge:
                days = {**current.get("days", {}), **data.get("days", {})}
                data = {**current, **data, "days": days}
            self.store.put(path, data)


class Db:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        return Collection(self.store, name)

    def transaction(self):
        return Tx(self.store)


def retrying(fn):
    """What firestore.transactional does: run again from the start if the commit is aborted."""
    def run(tx, *args):
        for _ in range(5):
            tx = Tx(tx.store)
            try:
                result = fn(tx, *args)
                tx.commit()
                return result
            except Aborted:
                continue
        raise Aborted("gave up")
    return run


ROOM = {"number": "202", "name": "Ocean Breeze", "type": "Sea View Deluxe", "price": 32000, "active": True,
        "amenities": ["Sea view", "Wi-Fi"], "checkIn": "14:00", "checkOut": "11:00", "notice": ""}


@pytest.fixture
def world():
    store = Store()
    store.put("rooms/r1", ROOM)
    store.put("rooms/r2", {**ROOM, "number": "101", "name": "Garden Terrace", "type": "Garden Room", "price": 18500})
    store.put("rooms/r3", {**ROOM, "number": "999", "active": False})
    store.put("users/u1", {"name": "Amara Perera"})
    store.put("users/u2", {"name": "Daniel Fernando"})
    with patch.object(bk, "today", return_value=date(2026, 11, 1)), \
         patch.object(bk.firestore, "transactional", retrying), \
         patch.object(bk.firestore, "SERVER_TIMESTAMP", "NOW"):
        yield store, Db(store)


def book(db, uid="u1", msg="m1", room="r1", a="2026-12-20", b="2026-12-23"):
    return bk.book_room(db, uid=uid, conversation_id="c-" + uid, message_id=msg, room_id=room, check_in=a, check_out=b)


def test_list_rooms_groups_active_rooms_by_type(world):
    _, db = world
    types = bk.list_rooms(db)["roomTypes"]
    assert set(types) == {"Sea View Deluxe", "Garden Room"}  # the inactive room is left out
    room = types["Sea View Deluxe"][0]
    assert room["pricePerNightLKR"] == 32000 and room["features"] == ["Sea view", "Wi-Fi"] and room["checkInTime"] == "14:00"


def test_find_rooms_hides_taken_nights_and_prices_the_stay(world):
    store, db = world
    free = bk.find_rooms(db, "2026-12-20", "2026-12-23")
    assert {r["roomId"] for r in free["freeRooms"]} == {"r1", "r2"}
    assert free["nights"] == 3 and {r["roomId"]: r["totalLKR"] for r in free["freeRooms"]}["r1"] == 96000
    book(db)
    assert {r["roomId"] for r in bk.find_rooms(db, "2026-12-22", "2026-12-24")["freeRooms"]} == {"r2"}
    assert {r["roomId"] for r in bk.find_rooms(db, "2026-12-23", "2026-12-24")["freeRooms"]} == {"r1", "r2"}  # check-out night is free
    assert [r["roomId"] for r in bk.find_rooms(db, "2026-12-20", "2026-12-23", "garden room")["freeRooms"]] == ["r2"]


def test_find_rooms_checks_across_a_month_end(world):
    _, db = world
    book(db, a="2026-12-31", b="2027-01-02")
    assert {r["roomId"] for r in bk.find_rooms(db, "2027-01-01", "2027-01-03")["freeRooms"]} == {"r2"}


@pytest.mark.parametrize("a,b,why", [
    ("2026-10-30", "2026-11-02", "past"),
    ("2026-12-20", "2026-12-20", "same day"),
    ("2026-12-20", "2026-12-19", "backwards"),
    ("2026-12-01", "2026-12-20", "too long"),
    ("2028-01-01", "2028-01-03", "too far ahead"),
    ("soon", "later", "not dates"),
])
def test_bad_stays_are_refused(world, a, b, why):
    _, db = world
    with pytest.raises(bk.BookingError):
        bk.find_rooms(db, a, b)
    with pytest.raises(bk.BookingError):
        book(db, a=a, b=b)


def test_book_room_saves_booking_and_marks_every_night(world):
    store, db = world
    made = book(db)
    assert made["status"] == "confirmed" and made["reference"].startswith("TB-") and made["totalLKR"] == 96000
    saved = store.docs[f"bookings/{bk.booking_id('m1', 'r1', '2026-12-20', '2026-12-23')}"]
    assert saved["uid"] == "u1" and saved["guestName"] == "Amara Perera" and saved["createdAt"] == "NOW"
    days = store.docs["availability/r1_2026-12"]["days"]
    assert set(days) == {"20", "21", "22"} and all(d["status"] == "booked" for d in days.values())
    assert made["reference"] in days["20"]["note"] and "Amara Perera" in days["20"]["note"]


def test_the_same_message_cannot_book_twice(world):
    store, db = world
    first, again = book(db), book(db)
    assert again["alreadyBooked"] and again["reference"] == first["reference"]
    assert sum(p.startswith("bookings/") for p in store.docs) == 1


def test_a_taken_night_stops_the_whole_booking(world):
    store, db = world
    book(db)
    with pytest.raises(bk.BookingError) as e:
        book(db, uid="u2", msg="m2", a="2026-12-22", b="2026-12-25")
    assert "2026-12-22" in e.value.message
    assert set(store.docs["availability/r1_2026-12"]["days"]) == {"20", "21", "22"}  # nothing from the failed booking
    assert sum(p.startswith("bookings/") for p in store.docs) == 1


def test_two_guests_booking_the_same_room_at_once_only_one_wins(world):
    store, db = world
    # Guest 2's booking lands between guest 1 reading the room and committing: Firestore aborts guest 1's commit.
    store.before_commit = lambda: store.put("availability/r1_2026-12", {"roomId": "r1", "month": "2026-12",
                                                                         "days": {"21": {"status": "booked"}}})
    with pytest.raises(bk.BookingError) as e:
        book(db)
    assert "2026-12-21" in e.value.message  # the retry saw the night guest 2 took
    assert not any(p.startswith("bookings/") for p in store.docs)


def test_inactive_missing_and_unknown_rooms_cannot_be_booked(world):
    _, db = world
    for room in ("r3", "nope"):
        with pytest.raises(bk.BookingError):
            book(db, room=room)


def test_a_guest_cannot_hold_more_than_the_cap(world):
    _, db = world
    for i in range(bk.MAX_UPCOMING_PER_GUEST):
        book(db, msg=f"m{i}", room="r1" if i % 2 == 0 else "r2", a=f"2026-12-{10 + i * 3}", b=f"2026-12-{12 + i * 3}")
    with pytest.raises(bk.BookingError):
        book(db, msg="more", room="r2", a="2027-02-01", b="2027-02-03")
    assert book(db, uid="u2", msg="other", room="r2", a="2027-02-01", b="2027-02-03")["guestName"] == "Daniel Fernando"


def test_confirmation_text_has_the_exact_details(world):
    _, db = world
    text = bk.confirmation_text(book(db))
    for part in ("TB-", "Ocean Breeze", "room 202", "3 nights", "20 December 2026", "23 December 2026",
                 "LKR 96,000", "LKR 32,000 per night", "from 14:00", "by 11:00"):
        assert part in text


def test_tools_never_raise_and_booking_needs_the_guests_yes(world):
    store, db = world
    booked = []
    with patch.object(tools.firestore, "client", return_value=db):
        t = {f.__name__: f for f in tools.make_tools("u1", "c1", "m1", booked)}
        assert set(t) == {"get_hotel_info", "list_rooms", "check_availability", "create_booking"}
        assert "error" in t["create_booking"]("r1", "2026-12-20", "2026-12-23", False) and not booked
        assert "error" in t["check_availability"]("2026-12-23", "2026-12-20")
        ok = t["create_booking"]("202", "2026-12-20", "2026-12-23", True)  # by room number, as Xenia has it
        assert ok["booked"] and len(booked) == 1
        t["create_booking"]("Ocean Breeze", "2026-12-20", "2026-12-23", True)  # a repeat, by name, is not confirmed twice
        assert len(booked) == 1
        taken = tools.make_tools("u2", "c2", "m2", [])[-1]("202", "2026-12-21", "2026-12-22", True)
        assert "error" in taken
        assert "error" in tools.make_tools("u2", "c2", "m3", [])[-1]("999", "2026-12-21", "2026-12-22", True)  # inactive room
        assert "error" in tools.make_tools("u2", "c2", "m4", [])[-1]("", "2026-12-21", "2026-12-22", True)
    with patch.object(tools.firestore, "client", side_effect=RuntimeError("down")):
        assert "error" in tools.make_tools("u1", "c1", "m9", [])[1]()


def test_resolve_room_by_number_name_or_id_and_only_active_rooms(world):
    _, db = world
    assert [bk.resolve_room(db, x) for x in ("202", " r1 ", "ocean breeze", "101")] == ["r1", "r1", "r1", "r2"]
    for bad in ("999", "nope", ""):
        with pytest.raises(bk.BookingError):
            bk.resolve_room(db, bad)
    db.store.put("rooms/r4", {**ROOM, "number": "303", "name": "Ocean Breeze"})  # same name as room 202
    with pytest.raises(bk.BookingError) as e:
        bk.resolve_room(db, "Ocean Breeze")
    assert "room number" in e.value.message
    assert bk.resolve_room(db, "303") == "r4"
