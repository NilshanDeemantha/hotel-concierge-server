from unittest.mock import MagicMock, patch

from messages import answer_message as task, hotel, reply


def test_hotel_tool_returns_each_topic_and_rejects_unknown():
    for topic in ("rooms", "dining_and_activities", "contact_and_times", "getting_here"):
        assert hotel.get_hotel_info(topic) == hotel.HOTEL[topic]
    assert "error" in hotel.get_hotel_info("prices")


@patch("messages.reply.GEMINI_API_KEY")
@patch("messages.reply.genai.Client")
def test_generate_reply_gives_the_model_the_one_tool(client, key):
    client().models.generate_content.return_value = MagicMock(text=" Hello! ")
    assert reply.generate_reply("hi") == "Hello!"
    config = client().models.generate_content.call_args.kwargs["config"]
    assert config.tools == [hotel.get_hotel_info]


@patch("messages.reply.GEMINI_API_KEY")
@patch("messages.reply.genai.Client")
def test_generate_reply_falls_back_on_empty_answer(client, key):
    client().models.generate_content.return_value = MagicMock(text=None)
    assert reply.generate_reply("hi") == reply.FALLBACK


@patch("messages.answer_message.functions.task_queue")
def test_enqueue_answer_delays_by_a_random_few_seconds(task_queue):
    task.enqueue_answer("m1")
    payload, options = task_queue("locations/asia-south1/functions/answer").enqueue.call_args.args
    assert payload == {"data": {"messageId": "m1"}}
    assert task.MIN_DELAY_SECONDS <= options.schedule_delay_seconds <= task.MAX_DELAY_SECONDS
    assert options.task_id == "m1"



@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.firestore")
def test_answer_message_types_then_saves_the_answer(firestore, reply_mod, chat_mod):
    db = firestore.client()
    db.collection().document().get().to_dict.return_value = {"conversationId": "c1", "uid": "u1", "content": "hi"}
    db.collection().document().get().exists = False
    reply_mod.generate_reply.return_value = "Hello!"
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    chat_mod.set_typing.assert_called_once()
    chat_mod.save_reply.assert_called_once_with(
        {"conversationId": "c1", "uid": "u1", "content": "hi"}, "m1", "Hello!"
    )


@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.firestore")
def test_answer_message_saves_an_apology_when_the_model_fails(firestore, reply_mod, chat_mod):
    db = firestore.client()
    db.collection().document().get().to_dict.return_value = {"conversationId": "c1", "uid": "u1", "content": "hi"}
    db.collection().document().get().exists = False
    reply_mod.generate_reply.side_effect = RuntimeError("quota")
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    assert chat_mod.save_reply.call_args.args[2] == reply_mod.FALLBACK


@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.firestore")
def test_answer_message_skips_a_message_already_answered(firestore, reply_mod, chat_mod):
    db = firestore.client()
    db.collection().document().get().to_dict.return_value = {"conversationId": "c1", "uid": "u1", "content": "hi"}
    db.collection().document().get().exists = True
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    reply_mod.generate_reply.assert_not_called()
