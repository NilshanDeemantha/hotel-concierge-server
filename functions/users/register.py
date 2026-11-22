"""The register function."""

from firebase_functions import https_fn

from common import POST_ONLY_CORS, json_response
from users import signup

MAX_BODY_BYTES = 2048


@https_fn.on_request(cors=POST_ONLY_CORS)
def register(req: https_fn.Request) -> https_fn.Response:
    """Creates an account, its profile and its conversation. Body: {name, phone?, email, password}."""
    if req.method != "POST":
        return json_response({"error": "Method not allowed"}, 405)
    if (req.content_length or 0) > MAX_BODY_BYTES:
        return json_response({"error": "Request too large"}, 413)
    try:
        details = signup.validate(req.get_json(silent=True))
        signup.check_rate_limit(signup.client_ip(req.headers.get("X-Forwarded-For")))
        signup.create_account(details)
    except signup.SignupError as e:
        return json_response({"error": e.message}, e.status)
    except Exception:
        # Details go to the function log only, never to the caller.
        print("register failed unexpectedly")
        import traceback

        traceback.print_exc()
        return json_response({"error": "Something went wrong. Please try again."}, 500)
    return json_response({"status": "created"}, 201)
