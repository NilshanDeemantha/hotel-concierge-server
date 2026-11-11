# Hotel concierge: server

Backend for the Thambili Bay Hotel concierge bot, a demo AI assistant for a fictional hotel. Python on Cloud Functions for Firebase. The web frontend lives in a separate repository.

## Layout

```
functions/
  main.py               every deployed function is defined here
  requirements.txt      runtime packages, pinned
  requirements-dev.txt  adds test tools
  tests/                pytest tests
firebase.json           tells the Firebase CLI what to deploy
```

## Run the tests

Needs Python 3.13.

```bash
cd functions
python3.13 -m venv venv
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest
```

## Deploy

```bash
firebase deploy --only functions --project thambili-bay-concierge
```

Functions run in `asia-south1` with at most one instance, to keep costs at zero.

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
