from unittest.mock import MagicMock, patch

import flask
import pytest
from firebase_admin.exceptions import FirebaseError
from werkzeug.test import EnvironBuilder

import main
from messages import chat

app = flask.Flask(__name__)
ORIGIN = "http://localhost:3000"


def post(body, token="Bearer good", method="POST", headers=None) -> flask.Response:
    headers = {"Origin": ORIGIN, **({"Authorization": token} if token else {}), **(headers or {})}
    builder = EnvironBuilder(method=method, path="/", json=body, headers=headers)
    with app.request_context(builder.get_environ()):
        return main.send_message(flask.request)


def test_validate_text_trims_and_checks():
    assert chat.validate_text({"text": "  hi  "}) == "hi"
    for bad in [None, [], {}, {"text": 5}, {"text": "   "}, {"text": "x" * (chat.MAX_MESSAGE_CHARS + 1)}]:
        with pytest.raises(chat.ChatError) as error:
            chat.validate_text(bad)
        assert error.value.status == 400
    assert chat.validate_text({"text": "x" * chat.MAX_MESSAGE_CHARS})


@patch("messages.send_message.enqueue_answer")
@patch("messages.send_message.chat.add_user_message")
@patch("messages.send_message.auth.verify_id_token")
def test_send_message_saves_for_the_verified_user(verify, add, enqueue):
    verify.return_value = {"uid": "uid-1"}
    add.return_value = {"messageId": "m1", "conversationId": "c1"}
    response = post({"text": " Hello "})
    assert response.status_code == 201
    assert response.get_json() == {"messageId": "m1", "conversationId": "c1"}
    verify.assert_called_once_with("good")
    add.assert_called_once_with("uid-1", "Hello")
    enqueue.assert_called_once_with("m1")


@patch("messages.send_message.chat.add_user_message")
@patch("messages.send_message.auth.verify_id_token")
def test_send_message_requires_a_valid_token(verify, add):
    assert post({"text": "hi"}, token=None).status_code == 401
    assert post({"text": "hi"}, token="Basic abc").status_code == 401
    verify.side_effect = ValueError("bad token")
    assert post({"text": "hi"}).status_code == 401
    verify.side_effect = FirebaseError("invalid-argument", "expired")
    assert post({"text": "hi"}).status_code == 401
    add.assert_not_called()


@patch("messages.send_message.chat.add_user_message")
@patch("messages.send_message.auth.verify_id_token")
def test_send_message_rejects_bad_input_and_methods(verify, add):
    verify.return_value = {"uid": "uid-1"}
    assert post({"text": ""}).status_code == 400
    assert post({"text": "x" * 5000}).status_code == 413
    assert post(None, method="GET").status_code == 405
    add.assert_not_called()


@patch("messages.send_message.chat.add_user_message")
@patch("messages.send_message.auth.verify_id_token")
def test_send_message_hides_internal_errors(verify, add):
    verify.return_value = {"uid": "uid-1"}
    add.side_effect = RuntimeError("firestore down")
    response = post({"text": "hi"})
    assert response.status_code == 500
    assert "firestore down" not in response.get_data(as_text=True)


def test_preflight_allows_the_authorization_header():
    response = post(
        None, token=None, method="OPTIONS",
        headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"},
    )
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "authorization" in response.headers["Access-Control-Allow-Headers"].lower()


@patch("messages.chat.firestore")
def test_add_user_message_creates_conversation_when_missing(firestore):
    db = firestore.client()
    conversations, messages = MagicMock(), MagicMock()
    db.collection.side_effect = {"conversations": conversations, "messages": messages}.get
    conversations.where().limit().get.return_value = []  # no conversation yet
    conversation = conversations.document()
    conversation.id = "c1"
    messages.document().id = "m1"
    assert chat.add_user_message("uid-1", "hi") == {"messageId": "m1", "conversationId": "c1"}
    assert conversation.set.call_args.args[0]["uid"] == "uid-1"
    batch = db.batch()
    saved = batch.set.call_args.args[1]
    assert (saved["uid"], saved["conversationId"], saved["content"], saved["role"]) == ("uid-1", "c1", "hi", "user")
    assert "seq" not in saved
    assert batch.update.call_args.args[0] is conversation
    batch.commit.assert_called_once()
