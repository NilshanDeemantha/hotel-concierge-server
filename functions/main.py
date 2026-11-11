"""Entry point. Every function the Firebase CLI deploys is defined in this file."""

import json

from firebase_functions import https_fn, options

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
