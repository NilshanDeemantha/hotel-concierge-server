from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from fakes import Db, Store
from messages import booking, memory, tools

AT = datetime(2026, 12, 1, 10, 0, tzinfo=booking.TZ)


@pytest.fixture
def store():
    return Store()


def save(store, note, at=AT):
    return memory.remember(Db(store), "u1", note, at)


def texts(store, at=AT):
    return [n["text"] for n in memory._read(Db(store), "u1", at)]


def test_a_note_is_saved_and_comes_back_in_the_prompt(store):
    assert save(store, "Vegetarian") == {"saved": True}
    t = memory.context("u1", Db(store), AT)
    assert "- Vegetarian" in t and "own words" in t and "bookings above are always right" in t


def test_nothing_is_added_when_the_guest_has_no_notes(store):
    assert memory.context("u1", Db(store), AT) == ""


def test_notes_are_short_and_whitespace_is_tidied(store):
    save(store, "  Arrives   late \n tonight " + "x" * 300)
    (n,) = texts(store)
    assert len(n) == memory.MAX_NOTE_CHARS and n.startswith("Arrives late tonight x")


def test_empty_secret_and_number_like_notes_are_refused(store):
    for bad in ("", "   ", "my password is hunter2", "card 4242 4242 4242 4242", "call me on 0771234567", "PIN 1234 please"):
        assert save(store, bad)["saved"] is False
    assert texts(store) == []


def test_a_repeat_is_not_saved_twice(store):
    save(store, "Vegetarian")
    assert save(store, "vegetarian")["duplicate"] is True
    assert texts(store) == ["Vegetarian"]


def test_only_the_newest_ten_are_kept(store):
    for i in range(12):
        save(store, f"note {i}")
    assert texts(store) == [f"note {i}" for i in range(2, 12)]


def test_old_notes_expire(store):
    save(store, "Vegetarian")
    assert texts(store, AT + memory.KEEP + timedelta(days=1)) == []


def test_each_guest_has_their_own_notes(store):
    save(store, "Vegetarian")
    assert memory.context("u2", Db(store), AT) == ""


def test_a_failed_read_gives_no_context():
    class Broken:
        def collection(self, name):
            raise RuntimeError("down")

    assert memory.context("u1", Broken(), AT) == ""


def test_the_tool_saves_for_the_guest_it_was_built_for_and_never_raises():
    with patch("messages.tools.firestore.client", return_value=Db(Store())) as client:
        remember = next(t for t in tools.make_tools("u1", "c1", "m1", [], []) if t.__name__ == "remember_guest_note")
        assert remember("Vegetarian") == {"saved": True}
        assert remember("my password is x")["saved"] is False
    with patch("messages.tools.firestore.client", side_effect=RuntimeError("down")):
        assert "error" in remember("Vegetarian")
