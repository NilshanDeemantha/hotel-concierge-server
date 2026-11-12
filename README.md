# Hotel concierge: server

Backend for the Thambili Bay Hotel concierge bot, a demo AI assistant for a fictional hotel. Python on Cloud Functions for Firebase. The web frontend lives in a separate repository.

## Layout

```
functions/
  main.py               every deployed function is defined here
  signup.py             sign-up rules: validation, rate limit, account creation
  requirements.txt      runtime packages, pinned
  requirements-dev.txt  adds test tools
  tests/                pytest tests
firebase.json           tells the Firebase CLI what to deploy
firestore.rules         database rules: no direct access from browsers
scripts/deploy.sh       production release: rules, then functions
```

## Endpoints

Base URL: `https://asia-south1-thambili-bay-concierge.cloudfunctions.net`

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health check |
| `POST /register` | Creates an account and its `users/{uid}` profile. JSON body: `name`, `phone` (optional), `email`, `password` (8 to 128 characters). Limited to 5 sign-ups per hour per address and 50 per hour in total. |

Browsers can call the API only from the site's own addresses and from `localhost`.

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

Functions run in `asia-south1` with at most one instance, to keep costs at zero. `firestore.rules` denies all direct access to the database; only this server reads and writes it.

## Secrets

Secrets live in Google Secret Manager, never in this repository:

```bash
firebase functions:secrets:set SECRET_NAME --project thambili-bay-concierge
```

## Workflow

- `master` is protected. Every change goes through a pull request.
- A pull request can be merged only after the owner adds the `approved` label.
- Branch names: `feat/server-0001`, `fix/server-0002`, `chore/server-0003`. The number increases by one for each change.

## License

No license. The source is public for review only. All rights reserved.
