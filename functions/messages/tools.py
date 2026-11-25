"""The tools Xenia can call: hotel facts, room details, free rooms for dates, and booking.

Built per answer, because booking needs to know which guest and which message is being answered. A tool never
raises: the model gets {"error": "..."} and explains it to the guest.
"""

import traceback

from firebase_admin import firestore

from messages import booking, hotel


def make_tools(uid: str, conversation_id: str, message_id: str, booked: list[dict], images: list[str]) -> list:
    """The tool functions for one guest message. Bookings that succeed are appended to `booked`, and photos to send
    with the answer to `images` (the model never writes image links itself)."""

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

    def show_room_photos(room: str, already_shown: int = 0) -> dict:
        """Sends photos of a room to the guest in the chat. Call it whenever the guest asks to see a room, its photos
        or pictures. The photos are attached to your answer automatically: never write links.

        Args:
            room: the room number (for example "001"), or the room name.
            already_shown: how many photos of this room the guest has already been sent, when they ask for more.
        """
        def run():
            found = booking.room_photos(firestore.client(), room, already_shown)
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
            return {"booked": True, "reference": made["reference"], "room": made["roomName"] or made["roomType"],
                    "totalLKR": made["totalLKR"], "note": "A confirmation message with the details is sent automatically."}

        return guarded(run)

    return [hotel.get_hotel_info, list_rooms, show_room_photos, check_availability, create_booking]
