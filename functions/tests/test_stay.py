from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from fakes import Db, Store
from messages import booking, reply, stay

DURING = datetime(2026, 12, 21, 19, 0, tzinfo=booking.TZ)
B = {"uid": "u1", "status": "confirmed", "reference": "TB-AAAAAA", "roomName": "Ocean Deluxe", "roomType": "Deluxe", "roomNumber": "202",
     "checkIn": "2026-12-20", "checkOut": "2026-12-23", "nights": 3, "totalLKR": 90000, "checkInTime": "14:00", "checkOutTime": "11:00"}


@pytest.fixture
def store():
    s = Store()
    s.put("bookings/b1", B)
    return s


def text(store, at=DURING):
    return stay.context("u1", Db(store), at)


def test_the_current_stay_names_room_dates_and_that_the_guest_is_staying(store):
    t = text(store)
    assert "TB-AAAAAA" in t and "Ocean Deluxe (room 202)" in t and "2026-12-20 to 2026-12-23" in t and "3 nights" in t
    assert "staying now" in t and "check-out by 11:00" in t


def test_an_upcoming_booking_is_listed_with_its_payment_state(store):
    store.put("bookings/b2", {**B, "reference": "TB-BBBBBB", "status": "payment_pending", "checkIn": "2027-01-10", "checkOut": "2027-01-12"})
    t = text(store)
    assert t.index("TB-AAAAAA") < t.index("TB-BBBBBB") and "waiting for payment" in t


def test_cancelled_past_and_other_guests_bookings_are_left_out(store):
    store.put("bookings/b2", {**B, "reference": "TB-CANCEL", "status": "cancelled"})
    store.put("bookings/b3", {**B, "reference": "TB-PAST", "checkIn": "2026-11-01", "checkOut": "2026-11-03"})
    store.put("bookings/b4", {**B, "reference": "TB-OTHER", "uid": "u2"})
    t = text(store)
    assert "TB-AAAAAA" in t and "TB-CANCEL" not in t and "TB-PAST" not in t and "TB-OTHER" not in t


def test_after_checkout_the_guest_is_said_to_have_no_bookings(store):
    assert "no current or upcoming bookings" in text(store, datetime(2026, 12, 24, 9, 0, tzinfo=booking.TZ))


def test_a_guest_with_no_bookings_is_told_so_but_a_failed_read_says_nothing():
    assert "no current or upcoming bookings" in stay.context("u9", Db(Store()), DURING)


def test_no_payment_details_or_name_are_included(store):
    store.put("bookings/b1", {**B, "stripeSessionId": "cs_secret", "guestName": "Amara"})
    t = text(store)
    assert "cs_secret" not in t and "Amara" not in t


def test_a_failed_read_gives_no_context_instead_of_an_error():
    class Broken:
        def collection(self, name):
            raise RuntimeError("down")

    assert stay.context("u1", Broken(), DURING) == ""


def test_the_reply_prompt_carries_the_guests_bookings():
    with patch("messages.reply.chat.recent_messages", return_value=[]), patch("messages.reply.GEMINI_API_KEY"), \
         patch("messages.reply.genai.Client") as client, patch("messages.reply.stay.context", return_value="BOOKINGS HERE"), \
         patch("messages.reply.memory.context", return_value="NOTES HERE"):
        client().models.generate_content.return_value = MagicMock(text="ok")
        reply.generate_reply({"conversationId": "c1", "uid": "u1", "content": "my room?"}, "m1", [], [])
    prompt = client().models.generate_content.call_args.kwargs["config"].system_instruction
    assert "BOOKINGS HERE\nNOTES HERE" in prompt
