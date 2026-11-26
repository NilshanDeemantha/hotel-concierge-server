"""The answer task: runs a few seconds after a message is saved and writes Xenia's answer."""

import random

from firebase_admin import firestore, functions
from firebase_functions import options, tasks_fn

from messages import booking, chat, reply
from payments import config, notices

REGION = "asia-south1"  # keep in step with main.py

# Xenia answers after a human-like pause.
MIN_DELAY_SECONDS = 2
MAX_DELAY_SECONDS = 6


def enqueue_answer(message_id: str) -> None:
    """Queues the answer for a random moment a few seconds from now. The message id makes the task unique."""
    delay = random.randint(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    functions.task_queue(f"locations/{REGION}/functions/answer").enqueue(
        {"data": {"messageId": message_id}},  # the handler reads what is under "data"
        functions.TaskOptions(schedule_delay_seconds=delay, task_id=message_id),
    )


@tasks_fn.on_task_dispatched(
    secrets=[reply.GEMINI_API_KEY, config.STRIPE_SECRET_KEY],
    # One try only: a retry would pay for the model twice. A failed call saves an apology instead.
    retry_config=options.RetryConfig(max_attempts=1),
    rate_limits=options.RateLimits(max_concurrent_dispatches=2),
)
def answer(req: tasks_fn.CallableRequest) -> None:
    message_id = req.data["messageId"]
    db = firestore.client()
    message = db.collection("messages").document(message_id).get().to_dict()
    if not message or db.collection("messages").document(chat.reply_id(message_id)).get().exists:
        return  # message gone, or already answered
    chat.set_typing(db.collection("conversations").document(message["conversationId"]), True)
    images: list[str] = []  # photos the model asked to send; saved with the answer
    resend: list[dict] = []  # bookings whose payment link the guest asked for again
    booked: list[dict] = []  # filled by the booking tool, so a booking is confirmed even if the model call then fails
    try:
        text = reply.generate_reply(message, message_id, booked, images, resend)
    except Exception:
        print("answer_message: the model call failed")
        import traceback

        traceback.print_exc()
        text = reply.FALLBACK
    if booked and text == reply.FALLBACK:
        text = reply.BOOKED_FALLBACK  # the apology would contradict the booking that was just made
    chat.save_reply(message, message_id, text, images=images)
    # Each confirmation is its own message with a fixed id, written by us (not the model) so the details are exact.
    for b in booked:
        chat.save_reply(message, message_id, booking.confirmation_text(b), doc_id=f"{message_id}-confirmation-{b['reference']}")
        # The payment link is a message of its own, after the details. The booking is held until it is paid.
        notices.send_payment_link(db, b, f"{message_id}-payment-{b['reference']}")
    for b in resend:
        notices.send_payment_link(db, b, f"{message_id}-payment-{b['reference']}")
