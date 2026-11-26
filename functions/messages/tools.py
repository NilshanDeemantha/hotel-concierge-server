"""The tools Xenia can call: hotel facts, room details, free rooms for dates, and booking.

Built per answer, because booking needs to know which guest and which message is being answered. A tool never
raises: the model gets {"error": "..."} and explains it to the guest.
"""

import traceback

from firebase_admin import firestore

from messages import booking, hotel


def make_tools(uid: str, conversation_id: str, message_id: str, booked: list[dict]) -> list:
    """The tool functions for one guest message. Bookings that succeed are appended to `booked`."""

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
            return {"booked": True, "reference": made["reference"], "room": made["roomName"] or made["roomType"],
                    "totalLKR": made["totalLKR"], "note": "A confirmation message with the details is sent automatically."}

        return guarded(run)

    return [hotel.get_hotel_info, list_rooms, check_availability, create_booking]
