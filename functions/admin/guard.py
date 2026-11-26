"""Shared wrapper for admin-only functions: method, size, admin check, JSON body, error replies."""

import traceback
from typing import Callable

from firebase_admin import auth
from firebase_admin.exceptions import FirebaseError
from firebase_functions import https_fn

from common import json_response

MAX_BODY_BYTES = 4096


class AdminError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def require_admin(req: https_fn.Request) -> str:
    """The uid of a signed-in admin, from the Bearer ID token (revocation checked)."""
    header = req.headers.get("Authorization", "")
    try:
        if not header.startswith("Bearer "):
            raise ValueError("missing token")
        claims = auth.verify_id_token(header[len("Bearer "):], check_revoked=True)
    except (ValueError, FirebaseError):
        raise AdminError(401, "Please log in again.")
    if claims.get("admin") is not True:
        raise AdminError(403, "Admin access required.")
    return claims["uid"]


def run(req: https_fn.Request, handler: Callable[[dict], dict]) -> https_fn.Response:
    if req.method != "POST":
        return json_response({"error": "Method not allowed"}, 405)
    if (req.content_length or 0) > MAX_BODY_BYTES:
        return json_response({"error": "Request too large"}, 413)
    try:
        require_admin(req)
        body = req.get_json(silent=True)
        if body is None and not req.get_data():
            body = {}
        if not isinstance(body, dict):
            raise AdminError(400, "Body must be a JSON object.")
        return json_response(handler(body))
    except AdminError as e:
        return json_response({"error": e.message}, e.status)
    except Exception:
        print("admin function failed unexpectedly")
        traceback.print_exc()
        return json_response({"error": "Something went wrong. Please try again."}, 500)
