"""Small pieces every function shares: who may call the API from a browser, and JSON replies."""

import json

from firebase_functions import https_fn, options

# Browsers may call the API only from the site itself or from local development.
ALLOWED_ORIGINS = [
    "https://thambili-bay-concierge.web.app",
    "https://thambili-bay-concierge.firebaseapp.com",
    "http://localhost:3000",
    "http://localhost:3001",
]

POST_ONLY_CORS = options.CorsOptions(cors_origins=ALLOWED_ORIGINS, cors_methods=["post"])


def json_response(body: dict, status: int = 200) -> https_fn.Response:
    return https_fn.Response(json.dumps(body), status=status, mimetype="application/json")
