"""The messages a guest gets about a payment. Written here, not by the model, so the details are exact."""

from google.api_core.exceptions import AlreadyExists

from messages import chat
from payments import checkout, config


def _send(booking: dict, doc_id: str, text: str, **extra) -> None:
    try:
        chat.save_notice(booking["conversationId"], booking["uid"], doc_id, text, **extra)
    except AlreadyExists:
        pass  # a repeated delivery or task: the guest already has it


def payment_text(booking: dict, link: dict) -> str:
    return (
        f"To keep your booking {booking['reference']} active, please pay LKR {booking['totalLKR']:,} with the secure "
        f"one-time payment link below. The link expires at {config.format_time(booking['paymentDeadline'])}. "
        "If the payment is not received by then, the booking is cancelled automatically and the room is released."
    )


def send_payment_link(db, booking: dict, doc_id: str) -> None:
    """A separate message with the payment link (shown as a Pay now button)."""
    try:
        link = checkout.link_for(db, booking)
    except checkout.PaymentError as e:
        _send(booking, doc_id, f"I could not send the payment link for booking {booking['reference']}: {e.message}")
        return
    except Exception:
        print("payment link failed")
        import traceback

        traceback.print_exc()
        _send(booking, doc_id, f"I could not create the payment link for booking {booking['reference']} just now. "
                               "Ask me to send it again, and pay before the booking expires.")
        return
    _send(booking, doc_id, payment_text(booking, link), payment={"url": link["url"], "expiresAt": link["expiresAt"]})


def paid(booking: dict) -> None:
    _send(booking, f"{booking['bookingId']}-paid",
          f"Payment received, thank you. Your booking {booking['reference']} is now confirmed: "
          f"{booking['roomName'] or booking['roomType']}, {booking['checkIn']} to {booking['checkOut']}.")


def cancelled(booking: dict) -> None:
    _send(booking, f"{booking['bookingId']}-cancelled",
          f"Your booking {booking['reference']} was cancelled because the payment was not received in time, and the room "
          "has been released. If you would still like to stay, ask me to check availability and book again.")


def refunded(booking: dict) -> None:
    _send(booking, f"{booking['bookingId']}-refunded",
          f"Your payment for booking {booking['reference']} arrived after the booking had been cancelled, so it has been "
          "refunded in full. It can take a few days to show on your card. Ask me to check availability to book again.")
