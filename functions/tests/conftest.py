import pytest


@pytest.fixture(autouse=True)
def no_real_bookings_lookup(monkeypatch, request):
    """generate_reply reads the guest's bookings for the prompt; tests must never reach a real Firestore.
    test_stay.py tests the real function against a fake database."""
    if request.module.__name__ != "test_stay":
        monkeypatch.setattr("messages.stay.context", lambda *a, **k: "")
