from unittest.mock import MagicMock, patch

import pytest

from admin import staff_message
from admin.guard import AdminError


@pytest.fixture
def db():
    with patch("admin.staff_message.firestore.client") as client:
        yield client.return_value


def test_rejects_bad_input(db):
    for body, status in [({"text": "hi"}, 400), ({"conversationId": "c1", "text": "  "}, 400),
                         ({"conversationId": "c1", "text": "x" * 1001}, 400)]:
        with pytest.raises(AdminError) as e:
            staff_message.send_staff_message(body)
        assert e.value.status == status


def test_unknown_conversation_is_404(db):
    db.collection.return_value.document.return_value.get.return_value = MagicMock(exists=False)
    with pytest.raises(AdminError) as e:
        staff_message.send_staff_message({"conversationId": "c1", "text": "hi"})
    assert e.value.status == 404


def test_saves_staff_message_for_the_guest(db):
    snap = MagicMock(exists=True)
    snap.to_dict.return_value = {"uid": "guest1"}
    db.collection.return_value.document.return_value.get.return_value = snap
    db.collection.return_value.document.return_value.id = "m1"
    assert staff_message.send_staff_message({"conversationId": "c1", "text": " hello "}) == {"messageId": "m1"}
    saved = db.batch.return_value.set.call_args[0][1]
    assert (saved["uid"], saved["role"], saved["content"]) == ("guest1", "staff", "hello")
    db.batch.return_value.commit.assert_called_once()
