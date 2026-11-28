from unittest.mock import MagicMock, patch

from google.api_core.exceptions import AlreadyExists

from messages import answer_message as task, hotel, menu, reply, tools


def test_menu_message_gives_the_hours_and_preparation_time_of_every_meal():
    text = menu.menu_text()
    for meal, t in menu.MEALS.items():
        assert meal in text.lower() and t["prep"] in text and t["served"] in text
    assert "PDF" in text and "LKR" in text and text.count("takes about") == 3


def test_menu_message_for_one_meal_mentions_only_that_meal():
    text = menu.menu_text("Lunch")
    assert menu.MEALS["lunch"]["prep"] in text and menu.MEALS["lunch"]["served"] in text
    assert "breakfast" not in text.lower() and "dinner" not in text.lower()
    assert menu.menu_text("brunch").count("takes about") == 3  # not a meal we serve: the whole menu


def test_the_menu_link_is_under_the_menus_folder_of_our_bucket():
    assert menu.MENU_PDF["url"].startswith(menu.URL_PREFIX)
    assert menu.URL_PREFIX.startswith("https://firebasestorage.googleapis.com/v0/b/thambili-bay-concierge.firebasestorage.app/")


@patch("messages.menu.chat.save_notice")
def test_send_saves_one_message_with_the_pdf_and_a_repeat_adds_nothing(save):
    message = {"conversationId": "c1", "uid": "u1"}
    menu.send(message, "m1", "dinner")
    save.assert_called_once_with("c1", "u1", "m1-menu", menu.menu_text("dinner"), document=menu.MENU_PDF)
    save.side_effect = AlreadyExists("done")
    menu.send(message, "m1", "dinner")  # a repeated task: no error, nothing more


def test_send_menu_tool_records_the_meal_once_and_ignores_unknown_meals():
    asked: list[str] = []
    send_menu = {f.__name__: f for f in tools.make_tools("u1", "c1", "m1", [], [], menus=asked)}["send_menu"]
    assert send_menu("Dinner")["sent"] is True
    assert send_menu("lunch")["sent"] is True
    assert asked == ["dinner"]  # one menu message per question, whatever the model calls
    other: list[str] = []
    {f.__name__: f for f in tools.make_tools("u1", "c1", "m2", [], [], menus=other)}["send_menu"]("midnight snack")
    assert other == [""]


def test_the_prompt_tells_xenia_to_use_the_tool_and_not_to_take_orders():
    assert "send_menu" in reply.SYSTEM_PROMPT and "place_dining_request" in reply.SYSTEM_PROMPT
    assert "à la carte" in reply.SYSTEM_PROMPT and "no buffet" in reply.SYSTEM_PROMPT
    contact = hotel.HOTEL["contact_and_times"]
    assert contact["phone"] in reply.SYSTEM_PROMPT and contact["email"] in reply.SYSTEM_PROMPT and "never make one up" in reply.SYSTEM_PROMPT
    dining = hotel.get_hotel_info("dining_and_activities")["dining"]
    assert "à la carte" in dining and "no buffet" in dining.lower()


@patch("messages.answer_message.menu")
@patch("messages.answer_message.notices", new=MagicMock())
@patch("messages.answer_message.chat")
@patch("messages.answer_message.reply")
@patch("messages.answer_message.firestore")
def test_answer_sends_the_menu_after_the_reply(firestore, reply_mod, chat_mod, menu_mod):
    db = firestore.client()
    message = {"conversationId": "c1", "uid": "u1", "content": "can I see the lunch menu"}
    db.collection().document().get().to_dict.return_value = message
    db.collection().document().get().exists = False
    reply_mod.FALLBACK, reply_mod.BOOKED_FALLBACK = "sorry", "booked"
    reply_mod.generate_reply.side_effect = lambda m, mid, booked, images, resend=None, cancelled=None, menus=None, dining_made=None: (menus.append("lunch"), "Sending it now.")[1]
    task.answer.__wrapped__(MagicMock(data={"messageId": "m1"}))
    chat_mod.save_reply.assert_called_once_with(message, "m1", "Sending it now.", images=[])
    menu_mod.send.assert_called_once_with(message, "m1", "lunch")
