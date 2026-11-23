# Hotel concierge: server

Backend for the Thambili Bay Hotel concierge bot, a demo AI assistant for a fictional hotel. Python on Cloud Functions for Firebase. The web frontend lives in a separate repository.

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
    hotel.py           the hotel details and the one tool Xenia uses to look them up
    chat.py            saving messages: conversation lookup, text rules, message documents
  admin/
    guard.py           shared check for every admin function: POST only, size limit, admin token, JSON, errors
    endpoints.py       the six admin functions
    conversations.py   lists conversations and reads their messages
    rooms.py           lists and saves rooms
    availability.py    reads and sets room availability per night
  requirements.txt      runtime packages, pinned
  requirements-dev.txt  adds test tools
  tests/                pytest tests
firebase.json           tells the Firebase CLI what to deploy
firestore.rules         database rules: no direct access from browsers
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
| `POST /admin_list_conversations` | Admin. The 100 most recently updated conversations with the guest's name. No body needed. |
| `POST /admin_get_messages` | Admin. The messages of one conversation. JSON body: `conversationId`. |
| `POST /admin_list_rooms` | Admin. All rooms, sorted by number. No body needed. |
| `POST /admin_save_room` | Admin. Creates a room, or updates it when `id` is given. JSON body: `number` (1 to 10 letters, digits or dashes, unique), `type`, `active` (optional, default `true`), `id` (optional). |
| `POST /admin_get_availability` | Admin. The day entries of every room for one month. JSON body: `month` (`YYYY-MM`). |
| `POST /admin_set_availability` | Admin. Sets a room's status for a range of nights. JSON body: `roomId`, `from` and `to` (`YYYY-MM-DD`, `to` is the end date and is not included, at most 60 nights), `status` (`booked`, `blocked` or `free`), `note` (optional, up to 200 characters). |

The `admin_*` endpoints need the Firebase ID token of an admin account as `Authorization: Bearer <token>`. They answer 401 without a valid token and 403 for an account that is not an admin.

`send_message` also queues the `answer` task (Cloud Tasks) to run 2 to 6 seconds later. It sets `typing` on the conversation, asks Gemini (with the hotel details tool) and saves the answer as a new message. The site listens to its own conversation and messages in Firestore, so the answer and the typing state arrive live.

Browsers can read only their own conversation and messages in Firestore (`firestore.rules`); they never write. Browsers can call the API only from the site's own addresses and from `localhost`.

## Make someone an admin

The account must already exist. The script uses Application Default Credentials (`gcloud auth application-default login`) for the `thambili-bay-concierge` project:

```bash
cd functions
venv/bin/python ../scripts/make_admin.py someone@example.com
venv/bin/python ../scripts/make_admin.py someone@example.com --revoke
```

The person must sign out and sign in again for the change to apply.

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

Functions run in `asia-south1` with at most one instance, to keep costs at zero. `firestore.rules` lets a signed-in user read only their own conversation and messages and denies every write; only this server writes.

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
