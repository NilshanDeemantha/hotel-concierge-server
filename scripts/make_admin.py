"""Grant or revoke the admin claim: python scripts/make_admin.py <email> [--revoke]

Uses Application Default Credentials (gcloud auth application-default login).
Other custom claims are kept. The user's refresh tokens are revoked so the change applies on their next sign-in.
"""

import argparse
import sys

import firebase_admin
from firebase_admin import auth

PROJECT = "thambili-bay-concierge"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()

    firebase_admin.initialize_app(options={"projectId": PROJECT})
    try:
        user = auth.get_user_by_email(args.email)
    except auth.UserNotFoundError:
        print(f"No user with email {args.email}", file=sys.stderr)
        return 1

    claims = dict(user.custom_claims or {})
    if args.revoke:
        claims.pop("admin", None)
    else:
        claims["admin"] = True
    auth.set_custom_user_claims(user.uid, claims or None)
    auth.revoke_refresh_tokens(user.uid)
    print(f"{'Revoked' if args.revoke else 'Granted'} admin for {args.email} ({user.uid}); they must sign in again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
