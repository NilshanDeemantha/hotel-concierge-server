"""Stripe payments for bookings: the payment link, the webhook, paying, not paying, and paying too late."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import flask
import pytest
import stripe
from fakes import Db, Store, retrying
from google.api_core.exceptions import AlreadyExists
from werkzeug.test import EnvironBuilder

import main  # noqa: F401  (also starts firebase_admin, as in test_chat)
from datetime import date

from messages import booking as bk
from payments import checkout, config, notices, policy, refunds, state, webhook

app = flask.Flask(__name__)
SECRET, HOOK_SECRET = "sk_test_123", "whsec_test"
NOW = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
LATER, EARLIER = NOW + timedelta(minutes=40), NOW - timedelta(minutes=5)


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", SECRET)
    monkeypatch.setenv("STRIPE_CHECKOUT_WEBHOOK_KEY", HOOK_SECRET)


def booking(**changes):
    return {
        "uid": "u1", "conversationId": "c1", "roomId": "r1", "guestName": "Amara", "checkIn": "2026-12-20",
        "checkOut": "2026-12-23", "nights": 3, "status": "payment_pending", "paymentDeadline": LATER,
        "bookingId": "b1", "reference": "TB-AAAAAA", "roomNumber": "202", "roomName": "Ocean Breeze",
        "roomType": "Sea View Deluxe", "pricePerNightLKR": 32000, "totalLKR": 96000,
        "checkInTime": "14:00", "checkOutTime": "11:00", "checkoutSessionId": "cs_1", **changes,
    }


@pytest.fixture
def world():
    store = Store()
    store.put("bookings/b1", booking())
    nights = {d: {"status": "booked", "note": "Booking TB-AAAAAA, Amara", "bookingId": "b1"} for d in ("20", "21", "22")}
    nights["24"] = {"status": "booked", "bookingId": "someone-else"}
    nights["05"] = {"status": "blocked", "note": "Repairs"}  # removed by staff, no booking
    store.put("availability/r1_2026-12", {"roomId": "r1", "month": "2026-12", "days": nights})
    sent = MagicMock()
    with patch.object(state.firestore, "transactional", retrying), patch.object(notices.chat, "save_notice", sent), patch.object(notices.chat, "set_payment_status"), \
         patch.object(checkout.auth, "get_user", return_value=MagicMock(email="amara@example.com")):
        yield store, Db(store), sent


def session(**changes):
    return {"id": "cs_1", "client_reference_id": "b1", "amount_total": 9_600_000, "currency": "lkr",
            "payment_status": "paid", "payment_intent": "pi_1",
            "customer_details": {"email": "amara@example.com"}, "payment_method_types": ["card"], **changes}


def days(store):
    return store.docs["availability/r1_2026-12"]["days"]


# ---- creating the payment link ----

def test_create_session_is_one_time_server_priced_and_expires_with_the_booking(world):
    store, db, _ = world
    created = MagicMock(id="cs_new", url="https://checkout.stripe.com/c/pay/cs_new")
    with patch.object(checkout.stripe.checkout.Session, "create", return_value=created) as create:
        link = checkout.create_session(db, booking(checkoutSessionId=None))
    kw = create.call_args.kwargs
    assert kw["mode"] == "payment" and kw["submit_type"] == "pay" and "subscription" not in json.dumps(kw, default=str)
    assert kw["line_items"][0]["price_data"]["unit_amount"] == 9_600_000 and kw["line_items"][0]["quantity"] == 1
    assert kw["line_items"][0]["price_data"]["currency"] == "lkr" and kw["client_reference_id"] == "b1"
    assert kw["expires_at"] == int(LATER.timestamp())
    assert kw["idempotency_key"] == "checkout-b1" and kw["api_key"] == SECRET
    assert kw["customer_email"] == "amara@example.com"
    assert kw["success_url"].startswith(config.SITE_URL + "/payment/success") and kw["cancel_url"].startswith(config.SITE_URL + "/payment/cancelled")
    assert link == {"url": created.url, "expiresAt": LATER.isoformat(), "sessionId": "cs_new"}
    assert store.docs["bookings/b1"]["checkoutSessionId"] == "cs_new"


def test_link_for_gives_the_open_link_again_and_refuses_when_nothing_can_be_paid(world):
    _, db, _ = world
    clock = patch.object(checkout, "_now", return_value=NOW)
    clock.start()
    open_session = MagicMock(id="cs_1", status="open", url="https://checkout.stripe.com/c/pay/cs_1")
    with patch.object(checkout.stripe.checkout.Session, "retrieve", return_value=open_session):
        assert checkout.link_for(db, booking())["url"] == open_session.url
        for bad in (booking(status="confirmed"), booking(status="cancelled"), booking(paymentDeadline=EARLIER)):
            with pytest.raises(checkout.PaymentError):
                checkout.link_for(db, bad)
    for closed in (MagicMock(id="cs_1", status="expired", url=None), MagicMock(id="cs_1", status="open", url="https://evil.example/pay")):
        with patch.object(checkout.stripe.checkout.Session, "retrieve", return_value=closed):
            with pytest.raises(checkout.PaymentError):  # an expired session, or a link that is not Stripe's
                checkout.link_for(db, booking())
    clock.stop()


# ---- being paid ----

def test_paying_confirms_the_booking_once_and_tells_the_guest(world):
    store, db, sent = world
    assert state.mark_paid(db, session()) == "confirmed_now"
    b = store.docs["bookings/b1"]
    assert b["status"] == "confirmed" and b["paymentIntentId"] == "pi_1" and "paidAt" in b
    assert state.mark_paid(db, session()) == "confirmed"  # the same payment again changes nothing
    assert sent.call_count == 1 and "now confirmed" in sent.call_args.args[3]
    assert sent.call_args.args[2] == "b1-paid"


@pytest.mark.parametrize("changes", [
    {"amount_total": 100}, {"currency": "usd"}, {"id": "cs_other"}, {"amount_total": 9_600_001},
])
def test_a_payment_that_does_not_match_the_booking_is_not_confirmed(world, changes):
    store, db, sent = world
    assert state.mark_paid(db, session(**changes)) == "mismatch"
    assert store.docs["bookings/b1"]["status"] == "payment_pending" and "paymentIssue" in store.docs["bookings/b1"]
    sent.assert_not_called()


def test_a_booking_whose_session_id_was_lost_is_still_confirmed_when_everything_else_matches(world):
    store, db, _ = world
    store.put("bookings/b1", booking(checkoutSessionId=None))
    assert state.mark_paid(db, session()) == "confirmed_now" and store.docs["bookings/b1"]["checkoutSessionId"] == "cs_1"


def test_an_unknown_booking_is_ignored(world):
    _, db, sent = world
    assert state.mark_paid(db, session(client_reference_id="nope")) == "unknown"
    sent.assert_not_called()


def test_paying_after_the_booking_was_cancelled_is_refunded_once(world):
    store, db, sent = world
    store.put("bookings/b1", booking(status="cancelled"))
    with patch.object(state.stripe.Refund, "create") as refund:
        assert state.mark_paid(db, session()) == "refunded"
        assert state.mark_paid(db, session()) == "cancelled"  # a repeat does not refund again
    refund.assert_called_once_with(payment_intent="pi_1", api_key=SECRET, idempotency_key="refund-b1")
    assert store.docs["bookings/b1"]["status"] == "cancelled" and "refundedAt" in store.docs["bookings/b1"]
    assert sent.call_count == 1 and "refunded in full" in sent.call_args.args[3]


# ---- not being paid ----

def test_cancelling_frees_only_the_nights_of_that_booking(world):
    store, db, sent = world
    assert state.cancel_unpaid(db, "b1", "payment_not_received") is True
    assert set(days(store)) == {"24", "05"}  # the other guest's night and the night staff removed stay
    b = store.docs["bookings/b1"]
    assert b["status"] == "cancelled" and b["cancelReason"] == "payment_not_received"
    assert state.cancel_unpaid(db, "b1", "again") is False  # nothing left to cancel
    assert sent.call_count == 1 and "cancelled" in sent.call_args.args[3] and sent.call_args.args[2] == "b1-cancelled"


def test_a_paid_booking_cannot_be_cancelled(world):
    store, db, sent = world
    state.mark_paid(db, session())
    sent.reset_mock()
    assert state.cancel_unpaid(db, "b1", "payment_link_expired") is False
    assert set(days(store)) == {"20", "21", "22", "24", "05"} and store.docs["bookings/b1"]["status"] == "confirmed"
    sent.assert_not_called()


def test_the_sweep_cancels_only_overdue_bookings_and_closes_their_link(world):
    store, db, _ = world
    store.put("bookings/b2", booking(bookingId="b2", reference="TB-BBBBBB", paymentDeadline=NOW + timedelta(hours=2),
                                     checkoutSessionId="cs_2", checkIn="2027-01-05", checkOut="2027-01-06"))
    with patch.object(checkout.stripe.checkout.Session, "expire") as expire:
        done = state.expire_overdue(db, NOW + timedelta(minutes=41))  # b1 is overdue (40 min), b2 is not
    assert done == {"cancelled": 1, "paid": 0}
    expire.assert_called_once_with("cs_1", api_key=SECRET)
    assert store.docs["bookings/b1"]["status"] == "cancelled" and store.docs["bookings/b2"]["status"] == "payment_pending"


def test_the_sweep_confirms_a_booking_paid_at_the_last_moment(world):
    store, db, _ = world
    paid = MagicMock(payment_status="paid")
    paid.to_dict.return_value = session()  # a Stripe object is turned into a plain dict before it is used
    with patch.object(checkout.stripe.checkout.Session, "expire", side_effect=stripe.InvalidRequestError("not open", "id")), \
         patch.object(checkout.stripe.checkout.Session, "retrieve", return_value=paid):
        done = state.expire_overdue(db, NOW + timedelta(minutes=41))
    assert done == {"cancelled": 0, "paid": 1} and store.docs["bookings/b1"]["status"] == "confirmed"


def test_the_sweep_cancels_when_the_link_had_already_expired_unpaid(world):
    store, db, _ = world
    with patch.object(checkout.stripe.checkout.Session, "expire", side_effect=stripe.InvalidRequestError("not open", "id")), \
         patch.object(checkout.stripe.checkout.Session, "retrieve", return_value=MagicMock(payment_status="unpaid")):
        assert state.expire_overdue(db, NOW + timedelta(minutes=41)) == {"cancelled": 1, "paid": 0}


# ---- the webhook ----

def post(payload: dict, secret=HOOK_SECRET, method="POST", signed=True, age=0):
    body = json.dumps(payload).encode()
    headers = {}
    if signed:
        t = int(time.time()) - age
        headers["Stripe-Signature"] = f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    with app.request_context(EnvironBuilder(method=method, path="/", data=body, headers=headers).get_environ()):
        return webhook.stripe_webhook(flask.request)


def event(kind="checkout.session.completed", id="evt_1", livemode=False, **obj):
    return {"id": id, "object": "event", "type": kind, "livemode": livemode, "data": {"object": session(**obj)}}


@pytest.fixture
def hook(world):
    store, db, sent = world
    with patch.object(webhook.firestore, "client", return_value=db):
        yield store, sent


def test_webhook_rejects_anything_that_is_not_a_correctly_signed_event(hook):
    store, sent = hook
    assert post(event(), method="GET").status_code == 405
    assert post(event(), signed=False).status_code == 400
    assert post(event(), secret="whsec_wrong").status_code == 400
    assert post(event(), age=3600).status_code == 400  # a captured old request cannot be replayed
    with app.request_context(EnvironBuilder(method="POST", path="/", data=b"not json",
                                            headers={"Stripe-Signature": "t=1,v1=00"}).get_environ()):
        assert webhook.stripe_webhook(flask.request).status_code == 400
    assert store.docs["bookings/b1"]["status"] == "payment_pending"
    sent.assert_not_called()


def test_webhook_confirms_a_paid_booking_and_ignores_a_repeated_event(hook):
    store, sent = hook
    first = post(event())
    assert first.status_code == 200 and first.get_json()["outcome"] == "confirmed_now"
    assert store.docs["bookings/b1"]["status"] == "confirmed"
    assert post(event()).get_json()["outcome"] == "duplicate"
    assert sent.call_count == 1 and "stripeEvents/evt_1" in store.docs


def test_webhook_waits_for_a_slow_payment_and_confirms_when_it_succeeds(hook):
    store, _ = hook
    assert post(event(payment_status="unpaid")).get_json()["outcome"] == "waiting"
    assert store.docs["bookings/b1"]["status"] == "payment_pending"
    assert post(event("checkout.session.async_payment_succeeded", id="evt_2")).get_json()["outcome"] == "confirmed_now"


@pytest.mark.parametrize("kind,reason", [("checkout.session.expired", "payment_link_expired"),
                                         ("checkout.session.async_payment_failed", "payment_failed")])
def test_webhook_cancels_the_booking_when_the_link_expires_or_the_payment_fails(hook, kind, reason):
    store, _ = hook
    assert post(event(kind, payment_status="unpaid")).get_json()["outcome"] == "cancelled"
    assert store.docs["bookings/b1"]["cancelReason"] == reason and set(days(store)) == {"24", "05"}


def test_webhook_ignores_other_events_and_events_of_the_other_mode(hook):
    store, sent = hook
    assert post(event("charge.refunded", id="evt_3")).get_json()["outcome"] == "ignored"
    assert post(event(id="evt_4", livemode=True)).get_json().get("ignored") == "mode"  # a live event on test keys
    assert store.docs["bookings/b1"]["status"] == "payment_pending"
    sent.assert_not_called()


def test_webhook_answers_500_so_stripe_retries_when_handling_fails(hook):
    store, _ = hook
    with patch.object(webhook.state, "mark_paid", side_effect=RuntimeError("firestore down")):
        assert post(event()).status_code == 500
    assert "stripeEvents/evt_1" not in store.docs  # not marked done, so the retry is processed


# ---- the messages ----

def test_the_payment_message_gives_the_amount_the_expiry_and_the_consequence(world):
    _, db, sent = world
    link = MagicMock()
    with patch.object(notices.checkout, "link_for", return_value={"url": "https://checkout.stripe.com/c/pay/x", "expiresAt": LATER.isoformat()}):
        notices.send_payment_link(db, booking(), "m1-payment-TB-AAAAAA")
    args, kwargs = sent.call_args
    assert args[:3] == ("c1", "u1", "m1-payment-TB-AAAAAA")
    for part in ("TB-AAAAAA", "LKR 96,000", "one-time", "expires at", "Sri Lanka time", "cancelled automatically", "released"):
        assert part in args[3]
    assert kwargs == {"payment": {"url": "https://checkout.stripe.com/c/pay/x", "expiresAt": LATER.isoformat()}, "paymentBookingId": "b1"}
    assert "https://" not in args[3]  # the link is the button, not text the model or a guest could change


def test_when_the_link_cannot_be_made_the_guest_is_told_and_can_ask_again(world):
    _, db, sent = world
    with patch.object(notices.checkout, "link_for", side_effect=RuntimeError("stripe down")):
        notices.send_payment_link(db, booking(), "d1")
    assert "Ask me to send it again" in sent.call_args.args[3] and "payment" not in sent.call_args.kwargs
    with patch.object(notices.checkout, "link_for", side_effect=checkout.PaymentError("The payment link is no longer open.")):
        notices.send_payment_link(db, booking(), "d2")
    assert "no longer open" in sent.call_args.args[3]


def test_a_repeated_notice_is_swallowed(world):
    _, db, sent = world
    sent.side_effect = AlreadyExists("again")
    notices.paid(booking())  # does not raise


def test_the_secret_key_and_event_mode_helpers():
    assert config.is_live_key("sk_live_abc") and config.is_live_key("rk_live_abc") and not config.is_live_key("sk_test_abc")
    assert config.minor_units(96000) == 9_600_000
    assert config.PAYMENT_WINDOW >= timedelta(minutes=30) and config.PAYMENT_WINDOW <= timedelta(hours=24)  # Stripe's limits


# ---- the payments collection and the 30 minute window ----

def test_the_payment_record_follows_the_booking_from_pending_to_paid_or_expired(world):
    store, db, _ = world
    created = MagicMock(id="cs_new", url="https://checkout.stripe.com/c/pay/cs_new")
    with patch.object(checkout.stripe.checkout.Session, "create", return_value=created) as create:
        checkout.create_session(db, booking(checkoutSessionId=None))
    record = store.docs["payments/b1"]
    assert record["status"] == "pending" and record["amountLKR"] == 96000 and record["amountMinor"] == 9_600_000
    assert record["checkoutSessionId"] == "cs_new" and record["reference"] == "TB-AAAAAA"
    assert not any(k in record for k in ("card", "number", "cvc"))  # card data never reaches us
    # Stripe needs 30 minutes at least, so its session may outlive our deadline (we close it ourselves)
    assert create.call_args.kwargs["expires_at"] >= int((checkout._now() + timedelta(minutes=30)).timestamp())

    store.docs["bookings/b1"]["checkoutSessionId"] = "cs_new"
    state.mark_paid(db, session(id="cs_new"))
    paid = store.docs["payments/b1"]
    assert paid["status"] == "paid" and paid["paymentIntentId"] == "pi_1"
    assert paid["amountPaidMinor"] == 9_600_000 and paid["currency"] == "lkr" and paid["customerEmail"] == "amara@example.com"
    assert paid["paymentMethodTypes"] == ["card"] and "stripeEventId" in paid

    store.put("bookings/b1", booking())
    store.docs["payments/b1"]["status"] = "pending"
    state.cancel_unpaid(db, "b1", "payment_not_received")
    assert store.docs["payments/b1"]["status"] == "expired"


def test_the_hold_is_30_minutes_and_the_sweep_runs_every_30_minutes():
    assert config.PAYMENT_WINDOW == timedelta(minutes=30)
    assert webhook.expire_unpaid_bookings.__firebase_endpoint__.scheduleTrigger["schedule"] == "every 30 minutes"


# ---- the refund policy and cancelling ----

@pytest.mark.parametrize("days, percent", [(60, 100), (30, 100), (29, 90), (21, 90), (20, 75), (14, 75), (13, 50), (7, 50), (6, 25), (0, 25)])
def test_the_refund_follows_the_days_before_check_in(days, percent):
    q = policy.quote("2026-12-20", 96000, date(2026, 12, 20) - timedelta(days=days))
    assert q["refundPercent"] == percent and q["deductPercent"] == 100 - percent and q["refundLKR"] == 96000 * percent // 100


def test_a_stay_that_has_started_gets_no_refund_and_cannot_be_cancelled(world):
    store, db, _ = world
    store.put("bookings/b1", booking(status="confirmed", paymentIntentId="pi_1"))
    with patch.object(refunds.bk, "today", return_value=date(2026, 12, 21)), pytest.raises(bk.BookingError):
        refunds.quote_for({**store.docs["bookings/b1"], "bookingId": "b1"})


def paid_booking(store, **changes):
    store.put("bookings/b1", booking(status="confirmed", paymentIntentId="pi_1", paidAt=NOW, **changes))
    store.put("payments/b1", {"status": "paid", "bookingId": "b1"})
    return {**store.docs["bookings/b1"], "bookingId": "b1"}


def test_cancelling_a_paid_booking_refunds_the_policy_amount_once_and_frees_its_nights(world):
    store, db, sent = world
    found = paid_booking(store)
    with patch.object(refunds.bk, "today", return_value=date(2026, 11, 21)), \
         patch.object(refunds.stripe.Refund, "create", return_value=MagicMock(id="re_1")) as refund:  # 29 days: 10% deducted
        q = refunds.cancel(db, found)
        with pytest.raises(bk.BookingError):
            refunds.cancel(db, found)  # a second cancel does nothing and refunds nothing
    refund.assert_called_once()
    assert refund.call_args.kwargs["amount"] == 8_640_000 and refund.call_args.kwargs["payment_intent"] == "pi_1"
    assert refund.call_args.kwargs["idempotency_key"] == "guest-refund-b1"
    b = store.docs["bookings/b1"]
    assert b["status"] == "cancelled" and b["refundLKR"] == 86400 and b["refundPercent"] == 90 and b["refundStatus"] == "done"
    assert store.docs["payments/b1"]["status"] == "partially_refunded" and store.docs["payments/b1"]["refundedAmountMinor"] == 8_640_000
    assert set(days(store)) == {"24", "05"}  # only this booking's nights were freed
    notices.guest_cancelled(found, q)
    text = sent.call_args.args[3]
    assert "LKR 86,400 of the LKR 96,000" in text and "10% is deducted" in text and "29 days" in text


def test_a_full_refund_when_cancelling_a_month_ahead(world):
    store, db, _ = world
    found = paid_booking(store)
    with patch.object(refunds.bk, "today", return_value=date(2026, 11, 20)), \
         patch.object(refunds.stripe.Refund, "create", return_value=MagicMock(id="re_1")) as refund:
        refunds.cancel(db, found)
    assert refund.call_args.kwargs["amount"] == 9_600_000 and store.docs["payments/b1"]["status"] == "refunded"


def test_when_stripe_fails_the_booking_stays_cancelled_and_the_refund_is_flagged(world):
    store, db, sent = world
    found = paid_booking(store)
    with patch.object(refunds.bk, "today", return_value=date(2026, 12, 15)), \
         patch.object(refunds.stripe.Refund, "create", side_effect=stripe.APIConnectionError("down")):
        q = refunds.cancel(db, found)  # within a week: 75% deducted
    assert q["refunded"] is False and q["refundLKR"] == 24000
    assert store.docs["bookings/b1"]["status"] == "cancelled" and store.docs["bookings/b1"]["refundStatus"] == "failed"
    assert store.docs["payments/b1"]["status"] == "refund_failed"
    notices.guest_cancelled(found, q)
    assert "could not be sent automatically" in sent.call_args.args[3] and "LKR 24,000" in sent.call_args.args[3]


def test_cancelling_an_unpaid_booking_releases_the_room_and_closes_the_link(world):
    store, db, sent = world
    found = {**store.docs["bookings/b1"], "bookingId": "b1"}
    with patch.object(refunds.checkout.stripe.checkout.Session, "expire") as expire, \
         patch.object(refunds.stripe.Refund, "create") as refund:
        q = refunds.cancel(db, found)
    expire.assert_called_once()
    refund.assert_not_called()
    assert q["refundLKR"] == 0 and store.docs["bookings/b1"]["status"] == "cancelled" and set(days(store)) == {"24", "05"}
    assert "as you asked" in sent.call_args.args[3]


def test_a_paid_event_repeated_after_a_guest_cancelled_is_not_refunded_again(world):
    store, db, _ = world
    paid_booking(store)
    store.put("bookings/b1", {**store.docs["bookings/b1"], "status": "cancelled"})
    with patch.object(state.stripe.Refund, "create") as refund:
        assert state.mark_paid(db, session()) == "cancelled"
    refund.assert_not_called()


def test_the_payment_message_and_the_hotel_info_give_the_refund_policy(world):
    from messages import hotel
    link = {"url": "https://checkout.stripe.com/c/pay/x", "expiresAt": LATER.isoformat()}
    text = notices.payment_text(booking(), link)
    for part in ("full refund", "10%", "25%", "50%", "75%"):
        assert part in text
    assert hotel.get_hotel_info("cancellation_and_refunds")["policy"] == policy.POLICY


def test_cancel_booking_needs_the_guests_yes_and_touches_nothing_without_it():
    from messages import tools
    cancel = {f.__name__: f for f in tools.make_tools("u1", "c1", "m1", [], [])}["cancel_booking"]
    assert "error" in cancel("TB-AAAAAA", False) and "error" in cancel("TB-AAAAAA", None)


def test_xenia_always_has_the_refund_policy_in_her_instructions():
    from messages import reply
    assert policy.POLICY in reply.SYSTEM_PROMPT  # the model skipped the tool once and made up terms


# ---- payment messages show Payment done / Link expired ----

def test_the_payment_message_is_marked_paid_when_paid_and_closed_when_cancelled_unpaid(world):
    store, db, sent = world
    with patch.object(checkout, "link_for", return_value={"url": "https://checkout.stripe.com/c/pay/x", "expiresAt": LATER.isoformat()}):
        notices.send_payment_link(db, {**booking(), "bookingId": "b1"}, "m1-payment-TB-AAAAAA")
    assert sent.call_args.kwargs["paymentBookingId"] == "b1"  # how the message is found again
    state.mark_paid(db, session())
    notices.chat.set_payment_status.assert_called_once_with("b1", "paid")

    notices.chat.set_payment_status.reset_mock()
    store.put("bookings/b1", booking())
    state.cancel_unpaid(db, "b1", "payment_not_received")
    notices.chat.set_payment_status.assert_called_once_with("b1", "closed")


def test_a_guest_cancelling_a_paid_booking_keeps_its_payment_message_as_paid(world):
    store, db, _ = world
    found = paid_booking(store)
    with patch.object(refunds.bk, "today", return_value=date(2026, 11, 21)), \
         patch.object(refunds.stripe.Refund, "create", return_value=MagicMock(id="re_1")):
        notices.guest_cancelled(found, refunds.cancel(db, found))
    notices.chat.set_payment_status.assert_not_called()


def test_set_payment_status_updates_every_payment_message_of_the_booking_and_never_raises():
    from messages import chat
    doc = MagicMock()
    client = MagicMock()
    client.collection().where().stream.return_value = [doc]
    with patch.object(chat.firestore, "client", return_value=client):
        chat.set_payment_status("b1", "paid")
    doc.reference.update.assert_called_once_with({"payment.status": "paid"})
    with patch.object(chat.firestore, "client", side_effect=RuntimeError("down")):
        chat.set_payment_status("b1", "paid")  # must not raise
