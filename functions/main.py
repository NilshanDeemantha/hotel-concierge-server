"""Entry point. Every function the Firebase CLI deploys is defined in this file."""

import json

import firebase_admin
from firebase_functions import https_fn, options

import signup

firebase_admin.initialize_app()

# Browsers may call the API only from the site itself or from local development.
ALLOWED_ORIGINS = [
    "https://thambili-bay-concierge.web.app",
    "https://thambili-bay-concierge.firebaseapp.com",
    "http://localhost:3000",
    "http://localhost:3001",
]
MAX_BODY_BYTES = 2048

# Cost guard: at most one small instance, in the region next to the Firestore database.
options.set_global_options(
    region="asia-south1",
    max_instances=1,
    memory=options.MemoryOption.MB_256,
)


@https_fn.on_request()
def health(req: https_fn.Request) -> https_fn.Response:
    """Health check: confirms the deployment is up. Touches no data and no secrets."""
    # Not named "healthz": Google's front end reserves that path and answers 404 itself.
    if req.method != "GET":
        return https_fn.Response("Method not allowed", status=405)
    return https_fn.Response(json.dumps({"status": "ok"}), mimetype="application/json")


def _json(body: dict, status: int = 200) -> https_fn.Response:
    return https_fn.Response(json.dumps(body), status=status, mimetype="application/json")


@https_fn.on_request(cors=options.CorsOptions(cors_origins=ALLOWED_ORIGINS, cors_methods=["post"]))
def register(req: https_fn.Request) -> https_fn.Response:
    """Creates an account and its profile. Body: {name, phone?, email, password}."""
    if req.method != "POST":
        return _json({"error": "Method not allowed"}, 405)
    if (req.content_length or 0) > MAX_BODY_BYTES:
        return _json({"error": "Request too large"}, 413)
    try:
        details = signup.validate(req.get_json(silent=True))
        signup.check_rate_limit(signup.client_ip(req.headers.get("X-Forwarded-For")))
        signup.create_account(details)
    except signup.SignupError as e:
        return _json({"error": e.message}, e.status)
    except Exception:
        # Details go to the function log only, never to the caller.
        print("register failed unexpectedly")
        import traceback

        traceback.print_exc()
        return _json({"error": "Something went wrong. Please try again."}, 500)
    return _json({"status": "created"}, 201)
