"""The tools Xenia can call: hotel facts, room details, free rooms for dates, and booking.

Built per answer, because booking needs to know which guest and which message is being answered. A tool never
raises: the model gets {"error": "..."} and explains it to the guest.
"""

import traceback

from firebase_admin import firestore

from messages import booking, hotel, menu
from payments import config as pay, refunds


def make_tools(
    uid: str, conversation_id: str, message_id: str, booked: list[dict], images: list[str],
    already_sent: frozenset = frozenset(), resend: list[dict] | None = None, cancelled: list[tuple] | None = None,
    menus: list[str] | None = None,
) -> list:
    """The tool functions for one guest message. Bookings that succeed are appended to `booked`, and photos to send
    with the answer to `images` (the model never writes image links itself). `already_sent` are the photos earlier
    messages of this conversation carried, so asking for more never repeats one. Bookings whose payment link the guest
    asked for again are appended to `resend`, bookings the guest cancelled to `cancelled` as (booking, refund quote), and a
    menu the guest asked for to `menus` as the meal they asked about ("" for all)."""
    resend = resend if resend is not None else []
    cancelled = cancelled if cancelled is not None else []
    menus = menus if menus is not None else []

    def guarded(fn):
        try:
            return fn()
        except booking.BookingError as e:
            print(f"xenia tool refused: {e.message}")  # so a wrong refusal can be found in the logs
            return {"error": e.message}
        except Exception:
            print("xenia tool failed")
            traceback.print_exc()
            return {"error": "Something went wrong on our side. Please try again or contact reception."}

    def list_rooms() -> dict:
        """Lists the rooms of the hotel that can be booked: for each room type, every room with its number, name,
        price per night in LKR, features and check-in and check-out times. Use it to describe rooms and prices."""
        return guarded(lambda: booking.list_rooms(firestore.client()))

    def show_room_photos(room: str) -> dict:
        """Sends photos of a room to the guest in the chat. Call it whenever the guest asks to see a room, its photos
        or pictures, also when they ask for more: each call sends photos they have not been sent yet. The photos are
        attached to your answer automatically: never write links.

        Args:
            room: the room number (for example "001"), or the room name.
        """
        def run():
            found = booking.room_photos(firestore.client(), room, set(already_sent) | set(images))
            images.extend(u for u in found["urls"] if u not in images)
            return {"sent": len(found["urls"]), "room": found["room"], "number": found["number"],
                    "morePhotosAvailable": found["remaining"]}

        return guarded(run)

    def check_availability(check_in: str, check_out: str, room_type: str = "") -> dict:
        """Finds the rooms that are free for every night of a stay, with the total price in LKR.

        Args:
            check_in: arrival date, YYYY-MM-DD.
            check_out: departure date, YYYY-MM-DD, after check_in. The night of the departure date is not used.
            room_type: optional room type name such as "Garden Room"; leave empty for all types.
        """
        return guarded(lambda: booking.find_rooms(firestore.client(), check_in, check_out, room_type))

    def create_booking(room: str, check_in: str, check_out: str, guest_has_confirmed: bool) -> dict:
        """Books one room for the guest. Call it as soon as the guest has said yes to the exact room, dates and total
        price: the room may be taken by then, and this call checks that. If it fails, tell the guest exactly what it
        returned and offer other rooms. Never say a room is unavailable or a booking failed without calling this.

        Args:
            room: the room number from check_availability (for example "202").
            check_in: arrival date, YYYY-MM-DD.
            check_out: departure date, YYYY-MM-DD.
            guest_has_confirmed: true only if the guest has clearly confirmed this exact booking in the chat.
        """
        print(f"xenia create_booking: room={room!r} {check_in}..{check_out} confirmed={guest_has_confirmed!r}")
        if guest_has_confirmed is not True:
            return {"error": "Ask the guest to confirm the room, dates and total price first."}

        def run():
            made = booking.book_room(
                firestore.client(), uid=uid, conversation_id=conversation_id, message_id=message_id,
                room_id=booking.resolve_room(firestore.client(), room), check_in=check_in, check_out=check_out,
            )
            if not any(b["reference"] == made["reference"] for b in booked):
                booked.append(made)
            return {"reserved": True, "reference": made["reference"], "room": made["roomName"] or made["roomType"],
                    "totalLKR": made["totalLKR"],
                    "payBy": pay.format_time(made["paymentDeadline"]),
                    "note": "The room is reserved but NOT confirmed until the guest pays. A message with the details and a "
                            "separate message with the payment link are sent automatically."}

        return guarded(run)

    def send_payment_link(reference: str) -> dict:
        """Sends the guest their payment link again, for a booking that is still waiting for payment. Call it when the
        guest asks for the link again or says they lost it. The link is sent as a separate message automatically:
        never write links yourself.

        Args:
            reference: the booking reference, for example "TB-1A2B3C".
        """
        def run():
            found = booking.find_guest_booking(firestore.client(), uid, reference)
            if found.get("status") != "payment_pending":
                return {"status": found.get("status"), "note": "Nothing is waiting for payment on this booking."}
            if not any(b["reference"] == found["reference"] for b in resend):
                resend.append(found)
            return {"status": "payment_pending", "note": "The payment link is being sent as a separate message."}

        return guarded(run)

    def check_cancellation(reference: str) -> dict:
        """Says what would happen if the guest cancelled this booking today: the refund in LKR and the percentage
        deducted under the refund policy. It changes nothing. Call it when the guest wants to cancel or asks what
        they would get back, then tell them the numbers and ask them to confirm before calling cancel_booking.

        Args:
            reference: the booking reference, for example "TB-1A2B3C".
        """
        return guarded(lambda: refunds.quote_for(booking.find_guest_booking(firestore.client(), uid, reference)))

    def cancel_booking(reference: str, guest_has_confirmed: bool) -> dict:
        """Cancels the guest's booking, releases the room and refunds what the refund policy allows. Call it only
        after check_cancellation, once the guest has clearly said yes to the refund amount. A message with the
        details is sent automatically.

        Args:
            reference: the booking reference, for example "TB-1A2B3C".
            guest_has_confirmed: true only if the guest has clearly confirmed the cancellation and the refund amount.
        """
        print(f"xenia cancel_booking: {reference!r} confirmed={guest_has_confirmed!r}")
        if guest_has_confirmed is not True:
            return {"error": "Tell the guest the refund amount and ask them to confirm the cancellation first."}

        def run():
            db = firestore.client()
            found = booking.find_guest_booking(db, uid, reference)
            q = refunds.cancel(db, found)
            cancelled.append((found, q))
            return {"cancelled": True, "refundLKR": q["refundLKR"],
                    "note": "The booking is cancelled. A message with the refund details follows automatically."}

        return guarded(run)

    def send_menu(meal: str = "") -> dict:
        """Sends the guest the à la carte menu as a PDF in the chat, with a short message that gives the preparation
        time and serving hours. Call it whenever the guest asks for the menu, the food or drink list, dishes or prices of
        food, or what they can eat. The PDF and that message are sent automatically: never write links or times yourself.

        Args:
            meal: "breakfast", "lunch" or "dinner" if the guest asked about one meal; leave empty for the whole menu.
        """
        if meal.strip().lower() not in ("", *menu.MEALS):
            meal = ""
        if not menus:
            menus.append(meal.strip().lower())
        return {"sent": True, "note": "The menu PDF is sent as a separate message with the preparation times. Say in one "
                                      "short sentence that you are sending it."}

    return [hotel.get_hotel_info, list_rooms, show_room_photos, check_availability, create_booking, send_payment_link,
            check_cancellation, cancel_booking, send_menu]
