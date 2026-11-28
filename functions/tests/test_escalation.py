from unittest.mock import MagicMock, patch

import pytest

from admin import hand_back, staff_message
from admin.guard import AdminError
from fakes import Db, Store
from messages import answer_message as task, escalation, reply, tools


@pytest.fixture
def store():
    s = Store()
    s.put("conversations/c1", {"uid": "u1", "messageCount": 3})
    return s


def conversation(store):
    return store.docs["conversations/c1"]


def test_escalating_marks_the_conversation_with_the_reason_and_the_time(store):
    assert escalation.escalate(Db(store), "c1", "  Asks if   pets are allowed ") == {"handedOver": True}
    c = conversation(store)
    assert c["needsAdmin"] is True and c["escalationReason"] == "Asks if pets are allowed" and "escalatedAt" in c


def test_an_empty_or_long_reason_is_tidied(store):
    escalation.escalate(Db(store), "c1", "x" * 500)
    assert len(conversation(store)["escalationReason"]) == escalation.MAX_REASON_CHARS
    store.put("conversations/c1", {"uid": "u1"})
    escalation.escalate(Db(store), "c1", "")
    assert conversation(store)["escalationReason"] == "Xenia could not answer"


def test_a_second_escalation_keeps_the_first_reason_and_time(store):
    escalation.escalate(Db(store), "c1", "first")
    first = dict(conversation(store))
    escalation.escalate(Db(store), "c1", "second")
    assert conversation(store) == first
    assert escalation.is_waiting(Db(store), "c1") is True


def test_a_conversation_nobody_flagged_is_not_waiting(store):
    assert escalation.is_waiting(Db(store), "c1") is False
    assert escalation.is_waiting(Db(store), "missing") is False


def test_the_tool_hands_over_and_tells_xenia_to_say_nothing_about_it(store):
    with patch("messages.tools.firestore.client", return_value=Db(store)):
        escalate = next(t for t in tools.make_tools("u1", "c1", "m1", [], []) if t.__name__ == "escalate_to_staff")
        result = escalate("Asks about pets")
    assert result["handedOver"] is True and conversation(store)["needsAdmin"] is True
    assert "Never mention staff" in result["note"] and "come back to them here" in result["note"]


def test_the_tool_never_raises():
    with patch("messages.tools.firestore.client", side_effect=RuntimeError("down")):
        escalate = next(t for t in tools.make_tools("u1", "c1", "m1", [], []) if t.__name__ == "escalate_to_staff")
        assert "error" in escalate("x")


@patch("messages.answer_message.notices", new=MagicMock())
@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.escalation")
@patch("messages.answer_message.firestore")
def test_xenia_stays_quiet_in_a_conversation_waiting_for_a_person(firestore, esc, reply_mod, chat_mod):
    db = firestore.client()
    db.collection().document().get().to_dict.return_value = {"conversationId": "c1", "uid": "u1", "content": "hi"}
    db.collection().document().get().exists = False
    esc.is_waiting.return_value = True
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    reply_mod.generate_reply.assert_not_called()
    chat_mod.set_typing.assert_not_called()
    chat_mod.save_reply.assert_not_called()


def test_a_staff_reply_hands_the_conversation_back_to_xenia():
    with patch("admin.staff_message.firestore.client") as client:
        db = client.return_value
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"uid": "guest1"}
        db.collection.return_value.document.return_value.get.return_value = snap
        staff_message.send_staff_message({"conversationId": "c1", "text": "Yes, small pets are welcome."})
    changes = db.batch.return_value.update.call_args[0][1]
    assert changes["needsAdmin"] is False and changes["escalationReason"] is None and changes["escalatedAt"] is None
    assert db.batch.return_value.set.call_args[0][1]["role"] == "staff"


def test_handing_back_without_a_reply_releases_the_conversation(store):
    escalation.escalate(Db(store), "c1", "pets?")
    with patch("admin.hand_back.firestore.client", return_value=Db(store)):
        assert hand_back.hand_back({"conversationId": "c1"}) == {"handedBack": True}
    assert conversation(store)["needsAdmin"] is False and not any(p.startswith("messages/") for p in store.docs)


def test_handing_back_checks_its_input(store):
    with patch("admin.hand_back.firestore.client", return_value=Db(store)):
        for body, status in (({}, 400), ({"conversationId": "nope"}, 404)):
            with pytest.raises(AdminError) as e:
                hand_back.hand_back(body)
            assert e.value.status == status


def test_the_prompt_sends_unanswerable_things_to_staff_without_telling_the_guest():
    p = reply.SYSTEM_PROMPT
    assert "escalate_to_staff" in p and "never mention staff" in p.lower()
    assert "tell the guest to tell reception" not in p and "point them to reception" not in p
    assert "Never send the guest to reception" in p
