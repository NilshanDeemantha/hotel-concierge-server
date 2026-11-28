from unittest.mock import MagicMock, patch

from messages import answer_message as task, hotel, reply


def test_hotel_tool_returns_each_topic_and_rejects_unknown():
    for topic in ("rooms", "dining_and_activities", "contact_and_times", "getting_here"):
        assert hotel.get_hotel_info(topic) == hotel.HOTEL[topic]
    assert "error" in hotel.get_hotel_info("prices")


MESSAGE = {"conversationId": "c1", "uid": "u1", "content": "hi"}


@patch("messages.reply.chat.recent_messages", return_value=[])
@patch("messages.reply.GEMINI_API_KEY")
@patch("messages.reply.genai.Client")
def test_generate_reply_gives_the_model_the_nine_tools(client, key, history):
    client().models.generate_content.return_value = MagicMock(text=" Hello! ")
    assert reply.generate_reply(MESSAGE, "m1", [], []) == "Hello!"
    config = client().models.generate_content.call_args.kwargs["config"]
    assert [f.__name__ for f in config.tools] == ["get_hotel_info", "list_rooms", "show_room_photos", "check_availability", "create_booking", "send_payment_link", "check_cancellation", "cancel_booking", "send_menu"]
    assert "Today is" in config.system_instruction


@patch("messages.reply.chat.recent_messages", return_value=[])
@patch("messages.reply.GEMINI_API_KEY")
@patch("messages.reply.genai.Client")
def test_generate_reply_falls_back_on_empty_answer(client, key, history):
    client().models.generate_content.return_value = MagicMock(text=None)
    assert reply.generate_reply(MESSAGE, "m1", [], []) == reply.FALLBACK


@patch("messages.reply.chat.recent_messages")
def test_history_starts_with_the_guest_and_ends_with_the_current_message(recent):
    recent.return_value = [{"role": "assistant", "content": "Welcome"}, {"role": "user", "content": "Rooms for 20 Dec?"},
                           {"role": "assistant", "content": "Which type?"}, {"role": "staff", "content": "Hi from staff"}]
    turns = reply._history(reply._rows(MESSAGE))
    assert [t.role for t in turns] == ["user", "model", "model", "user"]
    assert turns[-1].parts[0].text == "hi" and turns[0].parts[0].text == "Rooms for 20 Dec?"
    recent.return_value = []
    assert [t.parts[0].text for t in reply._history(reply._rows(MESSAGE))] == ["hi"]


@patch("messages.answer_message.functions.task_queue")
def test_enqueue_answer_delays_by_a_random_few_seconds(task_queue):
    task.enqueue_answer("m1")
    payload, options = task_queue("locations/asia-south1/functions/answer").enqueue.call_args.args
    assert payload == {"data": {"messageId": "m1"}}
    assert task.MIN_DELAY_SECONDS <= options.schedule_delay_seconds <= task.MAX_DELAY_SECONDS
    assert options.task_id == "m1"



@patch("messages.answer_message.notices", new=MagicMock())
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
        {"conversationId": "c1", "uid": "u1", "content": "hi"}, "m1", "Hello!", images=[]
    )


@patch("messages.answer_message.notices", new=MagicMock())
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


@patch("messages.answer_message.notices", new=MagicMock())
@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.firestore")
def test_answer_message_skips_a_message_already_answered(firestore, reply_mod, chat_mod):
    db = firestore.client()
    db.collection().document().get().to_dict.return_value = {"conversationId": "c1", "uid": "u1", "content": "hi"}
    db.collection().document().get().exists = True
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    reply_mod.generate_reply.assert_not_called()


@patch("messages.answer_message.booking.confirmation_text", return_value="CONFIRMED TB-1")
@patch("messages.answer_message.notices", new=MagicMock())
@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.firestore")
def test_answer_sends_a_confirmation_message_after_a_booking(firestore, reply_mod, chat_mod, confirmation):
    db = firestore.client()
    message = {"conversationId": "c1", "uid": "u1", "content": "yes please"}
    db.collection().document().get().to_dict.return_value = message
    db.collection().document().get().exists = False
    reply_mod.FALLBACK, reply_mod.BOOKED_FALLBACK = "sorry", "booked"
    booking_made = {"reference": "TB-1"}
    reply_mod.generate_reply.side_effect = lambda m, mid, booked, images, resend=None, cancelled=None, menus=None: (booked.append(booking_made), "All set!")[1]
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    assert chat_mod.save_reply.call_args_list[0].args == (message, "m1", "All set!")
    assert chat_mod.save_reply.call_args_list[1].args == (message, "m1", "CONFIRMED TB-1")
    assert chat_mod.save_reply.call_args_list[1].kwargs == {"doc_id": "m1-confirmation-TB-1"}


@patch("messages.answer_message.booking.confirmation_text", return_value="CONFIRMED TB-1")
@patch("messages.answer_message.notices", new=MagicMock())
@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.firestore")
def test_a_failed_model_call_after_a_booking_does_not_apologise_over_it(firestore, reply_mod, chat_mod, confirmation):
    db = firestore.client()
    message = {"conversationId": "c1", "uid": "u1", "content": "yes please"}
    db.collection().document().get().to_dict.return_value = message
    db.collection().document().get().exists = False
    reply_mod.FALLBACK, reply_mod.BOOKED_FALLBACK = "sorry", "booked"

    def fail_after_booking(m, mid, booked, images, resend=None, cancelled=None, menus=None):
        booked.append({"reference": "TB-1"})
        raise RuntimeError("model down")

    reply_mod.generate_reply.side_effect = fail_after_booking
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    assert chat_mod.save_reply.call_args_list[0].args[2] == "booked"
    assert chat_mod.save_reply.call_args_list[1].args[2] == "CONFIRMED TB-1"


@patch("messages.answer_message.notices", new=MagicMock())
@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.firestore")
def test_answer_saves_the_photos_the_model_asked_to_send(firestore, reply_mod, chat_mod):
    db = firestore.client()
    message = {"conversationId": "c1", "uid": "u1", "content": "show me room 001"}
    db.collection().document().get().to_dict.return_value = message
    db.collection().document().get().exists = False
    reply_mod.FALLBACK, reply_mod.BOOKED_FALLBACK = "sorry", "booked"
    reply_mod.generate_reply.side_effect = lambda m, mid, booked, images, resend=None, cancelled=None, menus=None: (images.extend(["u1", "u2"]), "Here is room 001.")[1]
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    chat_mod.save_reply.assert_called_once_with(message, "m1", "Here is room 001.", images=["u1", "u2"])


@patch("messages.reply.tools.make_tools", return_value=[])
@patch("messages.reply.chat.recent_messages")
@patch("messages.reply.GEMINI_API_KEY")
@patch("messages.reply.genai.Client")
def test_photos_already_sent_earlier_in_the_chat_are_passed_to_the_tools(client, key, recent, make_tools):
    recent.return_value = [{"role": "assistant", "content": "Photos", "images": ["a", "b"]}, {"role": "user", "content": "hi", "images": []}]
    client().models.generate_content.return_value = MagicMock(text="ok")
    reply.generate_reply(MESSAGE, "m1", [], [])
    assert make_tools.call_args.args[5] == frozenset({"a", "b"})


def test_the_prompt_makes_xenia_offer_a_booking_after_answering_about_a_room():
    assert "would like to book" in reply.SYSTEM_PROMPT and "do not ask again" in reply.SYSTEM_PROMPT
