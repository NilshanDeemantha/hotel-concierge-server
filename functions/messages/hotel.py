"""What Xenia knows about Thambili Bay, and the one tool she uses to look it up.

Keep these facts in step with the website (src/content in the web repository).
"""

from typing import Literal

Topic = Literal["rooms", "dining_and_activities", "contact_and_times", "getting_here"]

HOTEL = {
    "rooms": {
        "note": "Eighteen rooms, all with breakfast. Use list_rooms for the rooms, prices and features, and check_availability for free dates.",
        "room_types": [
            {"name": "Garden Room", "details": "Queen bed and a private terrace among the palms. Sleeps 2."},
            {"name": "Sea View Deluxe", "details": "King bed and a balcony over the bay. Sleeps 2."},
            {"name": "Family Suite", "details": "Two connected rooms for the whole family. Sleeps 4."},
        ],
    },
    "dining_and_activities": {
        "dining": "Breakfast on the terrace comes with every room. Lunch and dinner are served by the sea.",
        "spa": "Massages and treatments in a quiet garden room.",
        "pool": "A pool among the palms with loungers and shade, a few steps from the sand.",
        "boat_rides": "Morning and sunset rides along the coast, leaving from the bay in front of the hotel.",
        "whale_watching": "Early trips out to sea in season, with a chance to see whales and dolphins.",
        "galle_fort_walk": "A guided walk through the old fort: ramparts, the lighthouse and narrow streets of cafes.",
        "surf_lessons": "Lessons for beginners on the gentle breaks nearby, with boards provided.",
        "cooking_class": "Learn to cook a Sri Lankan rice and curry with the kitchen team, then eat it for lunch.",
    },
    "contact_and_times": {
        "address": "12 Beach Road, Galle 80000, Sri Lanka",
        "phone": "+94 91 000 0000",
        "email": "reception@thambilibay.example",
        "check_in": "From 2:00 pm",
        "check_out": "Until 11:00 am",
        "reception": "Open 24 hours",
        "note": "The hotel is fictional, so the phone number and email are placeholders.",
    },
    "getting_here": {
        "Galle Fort": "10 minutes by tuk-tuk",
        "Galle railway station": "15 minutes by car",
        "Colombo airport": "About 2 hours by the Southern Expressway",
    },
}


def get_hotel_info(topic: Topic) -> dict:
    """Looks up facts about Thambili Bay Hotel.

    Args:
        topic: rooms (room types), dining_and_activities (restaurant, spa, pool, trips and classes),
            contact_and_times (address, phone, email, check-in, check-out) or getting_here (travel times).
    """
    return HOTEL.get(topic, {"error": "Unknown topic"})
