"""Sign-up: validates the input, limits abuse, then creates the account and its profile."""

import re
import time
from collections import defaultdict, deque

from firebase_admin import auth, firestore

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[+0-9 ()-]{6,20}$")

WINDOW_SECONDS = 3600
MAX_PER_IP = 5
MAX_TOTAL = 50

# shortcut: counters live in memory, so they reset when the instance restarts.
# That is acceptable while max_instances is 1; move them to Firestore if that changes.
_attempts_by_ip: dict[str, deque[float]] = defaultdict(deque)
_all_attempts: deque[float] = deque()


class SignupError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def client_ip(forwarded_for: str | None) -> str:
    # Google's front end appends the real address last; anything before it is sent by the
    # caller and can be faked.
    return forwarded_for.split(",")[-1].strip() if forwarded_for else "unknown"


def check_rate_limit(ip: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    for attempts in (_attempts_by_ip[ip], _all_attempts):
        while attempts and attempts[0] <= now - WINDOW_SECONDS:
            attempts.popleft()
    if len(_attempts_by_ip[ip]) >= MAX_PER_IP or len(_all_attempts) >= MAX_TOTAL:
        raise SignupError(429, "Too many sign-ups. Please try again in an hour.")
    _attempts_by_ip[ip].append(now)
    _all_attempts.append(now)


def validate(data: object) -> dict[str, str]:
    if not isinstance(data, dict):
        raise SignupError(400, "Send the details as a JSON object.")

    def text(field: str) -> str:
        value = data.get(field, "")
        if not isinstance(value, str):
            raise SignupError(400, f"{field} must be text.")
        return value.strip()

    name, phone, email = text("name"), text("phone"), text("email").lower()
    password = data.get("password", "")

    if not 1 <= len(name) <= 80:
        raise SignupError(400, "Enter your name (up to 80 characters).")
    if phone and not PHONE_RE.fullmatch(phone):
        raise SignupError(400, "Enter a valid phone number, or leave it empty.")
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise SignupError(400, "Enter a valid email address.")
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise SignupError(400, "Use a password of 8 to 128 characters.")

    return {"name": name, "phone": phone, "email": email, "password": password}


def create_account(details: dict[str, str]) -> str:
    """Creates the Firebase account and its users/{uid} profile. Returns the uid."""
    try:
        user = auth.create_user(
            email=details["email"],
            password=details["password"],
            display_name=details["name"],
        )
    except auth.EmailAlreadyExistsError:
        raise SignupError(409, "An account with this email already exists. Try logging in.")

    profile = {"name": details["name"], "created_at": firestore.SERVER_TIMESTAMP}
    if details["phone"]:
        profile["phone"] = details["phone"]
    try:
        firestore.client().collection("users").document(user.uid).set(profile)
    except Exception:
        # Never leave an account without a profile.
        auth.delete_user(user.uid)
        raise
    return user.uid
