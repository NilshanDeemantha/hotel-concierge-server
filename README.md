# Hotel concierge: server

Backend for the Thambili Bay Hotel concierge bot, a demo AI assistant for a fictional hotel. Python on Cloud Functions for Firebase. The web frontend lives in a separate repository.

## Xenia room tools and bookings

Xenia can list the rooms (type, price, features, times), find the rooms free for given dates, and book one. A booking is made only after the guest says yes to the exact room, dates and total (the `create_booking` tool needs `guest_has_confirmed`). It writes the booking to `bookings` and marks every night as booked in `availability` in **one Firestore transaction**: if two guests ask for the same room and nights at the same moment, Firestore retries the second, which then sees the nights taken and gets a "no longer available" answer. The same message cannot book twice (the booking id comes from the message, room and dates), a guest can hold at most 3 upcoming bookings, a stay is at most 14 nights and at most 365 days ahead. After a booking the server (not the model) sends a second message with the exact details and a reference like `TB-1A2B3C`.

Needs `firestore.indexes.json` deployed (`messages` by `conversationId` and `createdAt`); until the index is built Xenia answers from the current message alone. Admins can read `bookings` (rules). Not built yet: payment, cancelling or changing a booking, a bookings screen in the admin site, and emailing the confirmation.

## Payments (Stripe, one-time)

After a booking Xenia's room is **reserved**, not confirmed. The server makes a Stripe Checkout Session (mode `payment`, one-time) for the booking's price, in LKR, and sends the guest a separate message with a **Pay now** button and the time the link expires (30 minutes, `PAYMENT_WINDOW` in `functions/payments/config.py`). Paying keeps the booking (`payment_pending` becomes `confirmed`, and the guest gets a receipt message). Not paying in time cancels it (`cancelled`), frees its nights and tells the guest.

How the money side is kept safe:

- Card details never reach us: the guest pays on a page hosted by Stripe.
- The API key and the webhook signing secret live in Secret Manager (`STRIPE_SECRET_KEY`, `STRIPE_CHECKOUT_WEBHOOK_KEY`), never in code or the web app.
- The price is worked out on the server from the room, never from the guest or the model; success and cancel addresses are fixed to our site.
- A booking becomes paid only through the `stripe_webhook` function, which checks Stripe's signature on the raw body (and rejects old, replayed requests) before reading anything, ignores events of the other mode (test events on a live key and the other way round), and processes each event id once. It also checks that the paid session is the one we made, for the booking's amount, in LKR.
- Stripe calls that change something use idempotency keys, so a retry never makes a second link or a second refund.
- Every state change is a Firestore transaction that checks the current state first, so a repeated webhook, the sweep and a payment at the last second cannot confirm or cancel twice. If a payment arrives after the booking was cancelled, it is refunded in full.
- `expire_unpaid_bookings` runs every 30 minutes as the backstop for the deadline (it expires the Stripe session first, so a link cannot be paid after the booking is cancelled). Stripe closes the session itself about 30 minutes after the booking (its minimum), and its `checkout.session.expired` event cancels the booking sooner than the sweep would. Each booking has a record in the `payments` collection (same id as the booking: Stripe session, payment intent, event and refund ids, the amount charged and the amount Stripe reports paid, currency, payer email, payment method type, `pending`/`paid`/`expired`/`refunded`/`mismatch`; no card data). A guest can hold at most 2 unpaid bookings.

Setup (test mode first):

1. In the Stripe dashboard (test mode) copy the secret key (`sk_test_...`) and run `firebase functions:secrets:set STRIPE_SECRET_KEY`.
2. Run `firebase functions:secrets:set STRIPE_CHECKOUT_WEBHOOK_KEY` with a placeholder, deploy the functions, and copy the URL of `stripe_webhook` from the deploy output.
3. In Stripe, Developers, Webhooks: add that URL as an endpoint with the events `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed` and `checkout.session.expired`. Copy its signing secret (`whsec_...`), set it with `firebase functions:secrets:set STRIPE_CHECKOUT_WEBHOOK_KEY` and redeploy `stripe_webhook`.
4. Pay with the test card `4242 4242 4242 4242`, any future expiry and any CVC.
5. For live: use the live keys and a live-mode endpoint with its own signing secret.

## Layout

```
functions/
  main.py               entry point: exports every deployed function, defines none
  common.py             shared pieces: allowed browser origins, JSON replies
  health.py             the health check function
  users/
    register.py        the register function
    signup.py          sign-up rules: validation, rate limit, account, profile and conversation
  messages/
    send_message.py    the send_message function
    answer_message.py  the answer task: runs a few seconds after a message and saves Xenia's answer
    reply.py           the Gemini call
    hotel.py           the hotel details Xenia looks up
    tools.py           the four tools Xenia can call: hotel facts, room list, free rooms for dates, booking
    booking.py         room search and the booking transaction, and the confirmation text
    chat.py            saving messages: conversation lookup, text rules, message documents
  admin/
    guard.py           shared check for every admin function: POST only, size limit, admin token, JSON, errors
    endpoints.py       the admin functions (writes only; admins read from Firestore directly)
    rooms.py           saves rooms
    availability.py    sets room availability per night
    staff_message.py   saves a staff reply in a guest's conversation
  requirements.txt      runtime packages, pinned
  requirements-dev.txt  adds test tools
  tests/                pytest tests
firebase.json           tells the Firebase CLI what to deploy
firestore.rules         database rules: who may read what from a browser; admins may write rooms and availability
scripts/deploy.sh       production release: rules, then functions
scripts/make_admin.py   grants or revokes the admin claim for an account
```

## Endpoints

Base URL: `https://asia-south1-thambili-bay-concierge.cloudfunctions.net`

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health check |
| `POST /register` | Creates an account, its `users/{uid}` profile and its conversation. JSON body: `name`, `phone` (optional), `email`, `password` (8 to 128 characters). Limited to 5 sign-ups per hour per address and 50 per hour in total. |
| `POST /send_message` | Saves the signed-in user's message in their conversation. Needs the Firebase ID token as `Authorization: Bearer <token>`. JSON body: `text` (1 to 1000 characters). |
| `POST /admin_save_room` | Admin. Creates a room, or updates it when `id` is given. JSON body: `number` (1 to 10 letters, digits or dashes, unique), `type` (`Garden Room`, `Sea View Deluxe` or `Family Suite`), `active` (optional, `true` or `false`, default `true`), `id` (optional, the room to update; 404 if it does not exist). |
| `POST /admin_set_availability` | Admin. Sets a room's status for a range of nights. JSON body: `roomId`, `from` and `to` (`YYYY-MM-DD`, `to` is the end date and is not included, at most 60 nights), `status` (`booked`, `blocked` or `free`), `note` (optional, up to 200 characters). |
| `POST /admin_send_message` | Admin. Saves a staff reply in a guest's conversation, which the guest's chat shows live. JSON body: `conversationId`, `text` (1 to 1000 characters). Saved with the role `staff`; 404 if the conversation does not exist. |

The `admin_*` endpoints need the Firebase ID token of an admin account as `Authorization: Bearer <token>`. They answer 401 without a valid token and 403 for an account that is not an admin.

`send_message` also queues the `answer` task (Cloud Tasks) to run 2 to 6 seconds later. It sets `typing` on the conversation, asks Gemini (with the last 12 messages of the conversation and the tools in `messages/tools.py`) and saves the answer as a new message. The site listens to its own conversation and messages in Firestore, so the answer and the typing state arrive live.

Browsers can read only their own conversation and messages in Firestore (`firestore.rules`). Admins (accounts with the `admin` claim) can additionally read all conversations, messages, users, rooms and availability, and write rooms and availability (valid documents only). Nothing else can be written from a browser.

The admin site reads those collections live from Firestore; only room, availability and staff-reply changes go through the `admin_*` endpoints. Deploy the rules before the site, or the site's reads are denied. Browsers can call the API only from the site's own addresses and from `localhost`.

## Make someone an admin

The account must already exist. The script uses Application Default Credentials (`gcloud auth application-default login`) for the `thambili-bay-concierge` project:

```bash
cd functions
venv/bin/python ../scripts/make_admin.py someone@example.com
venv/bin/python ../scripts/make_admin.py someone@example.com --revoke
```

The person must sign out and sign in again for the change to apply. Removing admin (`--revoke`) stops the admin functions at once, but the person's browser can still read the dashboard data through the Firestore rules until their sign-in token expires, up to an hour, because rules cannot check revocation.

## Run the tests

Needs Python 3.13.

```bash
cd functions
python3.13 -m venv venv
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest
```

## Run locally with the emulators

Runs Authentication, Firestore and the functions on your machine. Nothing touches the live project. Needs Java 21 or newer.

```bash
firebase emulators:start --project thambili-bay-concierge
```

On macOS, set these first or the function crashes when it creates an account:

```bash
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES NO_PROXY='*'
```

The endpoints are then at `http://127.0.0.1:5001/thambili-bay-concierge/asia-south1`.

## Deploy

Merging `master` into the `production` branch (through a pull request) runs the `release` workflow. It runs the tests, then `scripts/deploy.sh`, which deploys the Firestore rules first and the functions second.

To deploy by hand:

```bash
./scripts/deploy.sh
```

Functions run in `asia-south1` with at most one instance, to keep costs at zero. `firestore.rules` lets a signed-in user read only their own conversation and messages, lets admins also read conversations, messages, users, rooms and availability, and lets admins write rooms and availability; every other write is denied, so only this server writes the rest.

## Secrets

Secrets live in Google Secret Manager, never in this repository:

```bash
firebase functions:secrets:set SECRET_NAME --project thambili-bay-concierge
```

The Gemini key is the `GEMINI_API_KEY` secret. Enable the Secret Manager and Cloud Tasks APIs on the project before the first deploy. Locally the emulator reads secrets from `functions/.secret.local` (keep it out of git); without it Xenia answers with an apology.

## Workflow

- `master` is protected. Every change goes through a pull request.
- A pull request can be merged only after the owner adds the `approved` label.
- Branch names: `feat/server-0001`, `fix/server-0002`, `chore/server-0003`. The number increases by one for each change.

## License

No license. The source is public for review only. All rights reserved.

### Refund policy and cancelling

`functions/payments/policy.py` holds the policy (`TIERS`) and its text. Percent refunded by days before check-in: 30 or more days 100%, 21 to 29 days 90%, 14 to 20 days 75%, 7 to 13 days 50%, fewer than 7 days 25% (10%, 25%, 50% and 75% deducted). The text is in the payment message and in `get_hotel_info("cancellation_and_refunds")`, so Xenia quotes it when asked.

A guest cancels through Xenia: `check_cancellation` (no changes, gives the refund amount) then, after the guest says yes, `cancel_booking`. The booking becomes `cancelled`, only its nights are freed, and a partial Stripe refund of the policy amount goes to the original card (idempotency key `guest-refund-<bookingId>`). The booking and its `payments` record keep `refundLKR`, `refundPercent`, `refundStatus` (`done`/`failed`) and the Stripe refund id. If Stripe fails the cancellation stands, `refundStatus` is `failed` and the guest is told reception will send the refund: check bookings with `refundStatus == "failed"` by hand. A stay that has started cannot be cancelled by Xenia. Unpaid bookings cancel with nothing to refund.
