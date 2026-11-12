from unittest.mock import MagicMock, patch

import flask
import pytest
from werkzeug.test import EnvironBuilder

import main
import signup

# The CORS wrapper reads Flask's current request, which the Functions runtime normally provides.
app = flask.Flask(__name__)

VALID = {"name": " Nimal ", "phone": "+94 77 123 4567", "email": "Nimal@Example.com", "password": "correct-horse"}


@pytest.fixture(autouse=True)
def reset_rate_limit():
    signup._attempts_by_ip.clear()
    signup._all_attempts.clear()


def call(builder: EnvironBuilder) -> flask.Response:
    with app.request_context(builder.get_environ()):
        return main.register(flask.request)


def post(body, forwarded_for="203.0.113.9") -> flask.Response:
    return call(EnvironBuilder(method="POST", path="/", json=body, headers={"X-Forwarded-For": forwarded_for}))


def test_validate_cleans_input():
    assert signup.validate(VALID) == {
        "name": "Nimal", "phone": "+94 77 123 4567", "email": "nimal@example.com", "password": "correct-horse",
    }


@pytest.mark.parametrize("change", [
    {"name": ""}, {"name": "x" * 81}, {"name": 5},
    {"phone": "abc12345"}, {"phone": "123"},
    {"email": "not-an-email"}, {"email": "a@b"},
    {"password": "short"}, {"password": "x" * 129}, {"password": None},
])
def test_validate_rejects_bad_input(change):
    with pytest.raises(signup.SignupError) as error:
        signup.validate({**VALID, **change})
    assert error.value.status == 400


def test_validate_rejects_non_object():
    with pytest.raises(signup.SignupError):
        signup.validate(["not", "a", "dict"])


def test_phone_is_optional():
    assert signup.validate({**VALID, "phone": ""})["phone"] == ""


def test_client_ip_uses_last_entry_so_it_cannot_be_faked():
    assert signup.client_ip("1.2.3.4, 203.0.113.9") == "203.0.113.9"
    assert signup.client_ip(None) == "unknown"


def test_rate_limit_per_ip_and_window():
    for _ in range(signup.MAX_PER_IP):
        signup.check_rate_limit("a", now=1000)
    with pytest.raises(signup.SignupError) as error:
        signup.check_rate_limit("a", now=1001)
    assert error.value.status == 429
    signup.check_rate_limit("b", now=1001)  # another address is not affected
    signup.check_rate_limit("a", now=1000 + signup.WINDOW_SECONDS + 1)  # window has passed


def test_rate_limit_total():
    for i in range(signup.MAX_TOTAL):
        signup.check_rate_limit(f"ip-{i}", now=1000)
    with pytest.raises(signup.SignupError):
        signup.check_rate_limit("new-ip", now=1000)


@patch("signup.firestore")
@patch("signup.auth.create_user")
def test_register_creates_account_and_profile(create_user, firestore):
    create_user.return_value = MagicMock(uid="uid-1")
    response = post(VALID)
    assert response.status_code == 201
    create_user.assert_called_once_with(email="nimal@example.com", password="correct-horse", display_name="Nimal")
    document = firestore.client().collection("users").document
    document.assert_called_once_with("uid-1")
    document().set.assert_called_once_with(
        {"name": "Nimal", "phone": "+94 77 123 4567", "created_at": firestore.SERVER_TIMESTAMP}
    )


@patch("signup.auth.create_user")
def test_register_rejects_bad_input_without_creating(create_user):
    assert post({**VALID, "password": "short"}).status_code == 400
    create_user.assert_not_called()


@patch("signup.firestore")
@patch("signup.auth.create_user")
def test_register_reports_existing_email(create_user, firestore):
    create_user.side_effect = signup.auth.EmailAlreadyExistsError("exists", None, None)
    assert post(VALID).status_code == 409


@patch("signup.auth.delete_user")
@patch("signup.firestore")
@patch("signup.auth.create_user")
def test_register_removes_account_when_profile_fails(create_user, firestore, delete_user):
    create_user.return_value = MagicMock(uid="uid-1")
    firestore.client().collection().document().set.side_effect = RuntimeError("firestore down")
    response = post(VALID)
    assert response.status_code == 500
    assert "firestore down" not in response.get_data(as_text=True)
    delete_user.assert_called_once_with("uid-1")


@patch("signup.firestore")
@patch("signup.auth.create_user")
def test_register_is_rate_limited(create_user, firestore):
    create_user.return_value = MagicMock(uid="uid-1")
    for _ in range(signup.MAX_PER_IP):
        assert post(VALID).status_code == 201
    assert post(VALID).status_code == 429
    assert post(VALID, forwarded_for="9.9.9.9, 203.0.113.9").status_code == 429  # faked first entry


def test_register_rejects_get_and_large_bodies():
    assert call(EnvironBuilder(method="GET", path="/")).status_code == 405
    assert post({**VALID, "name": "x" * 5000}).status_code == 413
