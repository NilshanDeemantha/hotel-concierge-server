from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from admin import conversations
from admin.guard import AdminError


def doc(id, exists=True, **data):
    d = MagicMock(id=id, exists=exists)
    d.to_dict.return_value = data
    return d


def ts(n):
    return datetime(2026, 1, 1, 0, 0, n, tzinfo=timezone.utc)


@pytest.fixture
def db():
    with patch("admin.conversations.firestore.client") as client:
        yield client.return_value


def test_list_conversations_attaches_names_and_falls_back(db):
    q = db.collection.return_value.order_by.return_value.limit.return_value
    q.stream.return_value = [
        doc("c1", uid="u1", messageCount=3, updatedAt=ts(2)),
        doc("c2", uid="u2", messageCount=1, updatedAt=None),
    ]
    db.get_all.return_value = [doc("u1", name="Ann"), doc("u2", exists=False)]
    out = conversations.list_conversations({})
    assert out == {"conversations": [
        {"id": "c1", "uid": "u1", "name": "Ann", "messageCount": 3, "updatedAt": ts(2).isoformat()},
        {"id": "c2", "uid": "u2", "name": "Unknown guest", "messageCount": 1, "updatedAt": None},
    ]}
    assert db.get_all.call_count == 1


def test_list_conversations_orders_by_updated_desc_and_limits_100(db):
    db.collection.return_value.order_by.return_value.limit.return_value.stream.return_value = []
    assert conversations.list_conversations({}) == {"conversations": []}
    db.collection.assert_called_with("conversations")
    db.collection.return_value.order_by.assert_called_once_with("updatedAt", direction="DESCENDING")
    db.collection.return_value.order_by.return_value.limit.assert_called_once_with(100)


def test_get_messages_validates_and_404s(db):
    for bad in [{}, {"conversationId": ""}, {"conversationId": 5}]:
        with pytest.raises(AdminError) as e:
            conversations.get_messages(bad)
        assert e.value.status == 400
    db.collection.return_value.document.return_value.get.return_value = MagicMock(exists=False)
    with pytest.raises(AdminError) as e:
        conversations.get_messages({"conversationId": "x"})
    assert e.value.status == 404


def test_get_messages_sorted_oldest_first_pending_last(db):
    db.collection.return_value.document.return_value.get.return_value = MagicMock(exists=True)
    db.collection.return_value.where.return_value.stream.return_value = [
        doc("m3", role="assistant", content="c", createdAt=None),
        doc("m2", role="assistant", content="b", createdAt=ts(5)),
        doc("m1", role="user", content="a", createdAt=ts(1)),
    ]
    out = conversations.get_messages({"conversationId": "c1"})["messages"]
    assert [m["id"] for m in out] == ["m1", "m2", "m3"]
    assert out[0] == {"id": "m1", "role": "user", "content": "a", "createdAt": ts(1).isoformat()}
    assert out[2]["createdAt"] is None
