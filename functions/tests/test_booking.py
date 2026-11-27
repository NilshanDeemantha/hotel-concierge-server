"""Xenia's room tools and booking, run against a small in-memory Firestore that checks transactions the way
Firestore does: a commit is refused (Aborted) if a document the transaction read has changed since."""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fakes import Db, Store, retrying
from messages import booking as bk
from messages import tools


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
    assert made["status"] == "payment_pending" and made["reference"].startswith("TB-") and made["totalLKR"] == 96000
    assert made["paymentDeadline"] > datetime.now(timezone.utc) and made["bookingId"]
    saved = store.docs[f"bookings/{bk.booking_id('m1', 'r1', '2026-12-20', '2026-12-23')}"]
    assert saved["uid"] == "u1" and saved["guestName"] == "Amara Perera" and saved["createdAt"] == "NOW"
    days = store.docs["availability/r1_2026-12"]["days"]
    assert set(days) == {"20", "21", "22"} and all(d["status"] == "booked" for d in days.values())
    assert {d["bookingId"] for d in days.values()} == {made["bookingId"]}  # so cancelling frees only these nights
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
    with patch.object(bk.pay, "MAX_PENDING_PER_GUEST", 99):  # this test is about the total cap, not unpaid ones
        _cap(db)


def test_a_guest_cannot_hold_more_unpaid_bookings_than_the_pending_cap(world):
    store, db = world
    for i in range(bk.pay.MAX_PENDING_PER_GUEST):
        book(db, msg=f"p{i}", room="r1" if i % 2 == 0 else "r2", a=f"2026-12-{10 + i * 3}", b=f"2026-12-{12 + i * 3}")
    with pytest.raises(bk.BookingError) as e:
        book(db, msg="one-more", room="r2", a="2027-02-01", b="2027-02-03")
    assert "waiting for payment" in e.value.message
    for path, data in store.docs.items():  # once they are paid, the guest can book again
        if path.startswith("bookings/"):
            store.put(path, {**data, "status": "confirmed"})
    assert book(db, msg="after-paying", room="r2", a="2027-02-01", b="2027-02-03")["status"] == "payment_pending"
    for path, data in store.docs.items():  # a cancelled booking counts for nothing
        if path.startswith("bookings/"):
            store.put(path, {**data, "status": "cancelled"})
    assert book(db, msg="after-cancel", room="r1", a="2027-03-01", b="2027-03-02")


def _cap(db):
    for i in range(bk.MAX_UPCOMING_PER_GUEST):
        book(db, msg=f"m{i}", room="r1" if i % 2 == 0 else "r2", a=f"2026-12-{10 + i * 3}", b=f"2026-12-{12 + i * 3}")
    with pytest.raises(bk.BookingError):
        book(db, msg="more", room="r2", a="2027-02-01", b="2027-02-03")
    assert book(db, uid="u2", msg="other", room="r2", a="2027-02-01", b="2027-02-03")["guestName"] == "Daniel Fernando"


def test_confirmation_text_has_the_exact_details(world):
    _, db = world
    text = bk.confirmation_text(book(db))
    for part in ("TB-", "Ocean Breeze", "room 202", "3 nights", "20 December 2026", "23 December 2026",
                 "LKR 96,000", "LKR 32,000 per night", "from 14:00", "by 11:00", "reserved", "will be confirmed when you pay",
                 "Sri Lanka time", "cancelled automatically"):
        assert part in text


def test_tools_never_raise_and_booking_needs_the_guests_yes(world):
    store, db = world
    booked = []
    with patch.object(tools.firestore, "client", return_value=db):
        t = {f.__name__: f for f in tools.make_tools("u1", "c1", "m1", booked, [])}
        assert set(t) == {"get_hotel_info", "list_rooms", "show_room_photos", "check_availability", "create_booking", "send_payment_link", "check_cancellation", "cancel_booking"}
        assert "error" in t["create_booking"]("r1", "2026-12-20", "2026-12-23", False) and not booked
        assert "error" in t["check_availability"]("2026-12-23", "2026-12-20")
        ok = t["create_booking"]("202", "2026-12-20", "2026-12-23", True)  # by room number, as Xenia has it
        assert ok["reserved"] and "payBy" in ok and len(booked) == 1
        t["create_booking"]("Ocean Breeze", "2026-12-20", "2026-12-23", True)  # a repeat, by name, is not confirmed twice
        assert len(booked) == 1
        taken = tools.make_tools("u2", "c2", "m2", [], [])[4]("202", "2026-12-21", "2026-12-22", True)
        assert "error" in taken
        assert "error" in tools.make_tools("u2", "c2", "m3", [], [])[4]("999", "2026-12-21", "2026-12-22", True)  # inactive room
        assert "error" in tools.make_tools("u2", "c2", "m4", [], [])[4]("", "2026-12-21", "2026-12-22", True)
    with patch.object(tools.firestore, "client", side_effect=RuntimeError("down")):
        assert "error" in tools.make_tools("u1", "c1", "m9", [], [])[1]()


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


BUCKET = "https://firebasestorage.googleapis.com/v0/b/thambili-bay-concierge.firebasestorage.app/o/rooms%2Fr1%2F"


def photos(n, extra=()):
    return [f"{BUCKET}p{i}.jpg?alt=media&token=t" for i in range(n)] + list(extra)


def test_room_photos_are_limited_only_from_our_bucket_and_never_repeated(world):
    store, db = world
    store.put("rooms/r1", {**ROOM, "photos": photos(7, ["https://evil.example/x.jpg", "http://insecure", 5])})
    first = bk.room_photos(db, "202")
    assert first["urls"] == photos(4) and first["total"] == 7 and first["remaining"] == 3 and first["number"] == "202"
    more = bk.room_photos(db, "202", set(first["urls"]))  # the next ones, none twice
    assert more["urls"] == photos(7)[4:] and more["remaining"] == 0
    with pytest.raises(bk.BookingError):
        bk.room_photos(db, "202", set(photos(7)))  # all sent
    with pytest.raises(bk.BookingError):
        bk.room_photos(db, "101")  # no photos at all
    with pytest.raises(bk.BookingError):
        bk.room_photos(db, "999")  # inactive room


def test_show_room_photos_tool_collects_urls_for_the_answer_and_never_raises(world):
    store, db = world
    store.put("rooms/r1", {**ROOM, "photos": photos(6)})
    sent = []
    with patch.object(tools.firestore, "client", return_value=db):
        t = {f.__name__: f for f in tools.make_tools("u1", "c1", "m1", [], sent)}
        got = t["show_room_photos"]("202")
        assert got == {"sent": 4, "room": "Ocean Breeze", "number": "202", "morePhotosAvailable": 2}
        assert sent == photos(4)
        t["show_room_photos"]("202")
        assert sent == photos(6)  # the next ones are added, none twice
        assert "error" in t["show_room_photos"]("202")  # nothing left to send
        earlier = {t_ for t_ in photos(2)}  # photos sent in earlier messages of the chat are skipped too
        only = tools.make_tools("u1", "c1", "m2", [], [], frozenset(earlier))[2]("202")
        assert only["sent"] == 4
        assert "error" in t["show_room_photos"]("nope")
    with patch.object(tools.firestore, "client", side_effect=RuntimeError("down")):
        assert "error" in tools.make_tools("u1", "c1", "m9", [], [])[2]("202")
