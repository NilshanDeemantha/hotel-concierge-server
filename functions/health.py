"""Health check."""

from firebase_functions import https_fn

from common import json_response


@https_fn.on_request()
def health(req: https_fn.Request) -> https_fn.Response:
    """Health check: confirms the deployment is up. Touches no data and no secrets."""
    # Not named "healthz": Google's front end reserves that path and answers 404 itself.
    if req.method != "GET":
        return https_fn.Response("Method not allowed", status=405)
    return json_response({"status": "ok"})
