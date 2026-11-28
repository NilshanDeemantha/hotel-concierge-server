from unittest.mock import patch

import flask
import pytest
from werkzeug.test import EnvironBuilder

from firebase_functions import https_fn

import main


def call(method: str) -> https_fn.Response:
    request = https_fn.Request(EnvironBuilder(method=method, path="/").get_environ())
    return main.health(request)


def test_health_returns_ok():
    response = call("GET")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_health_rejects_other_methods():
    assert call("POST").status_code == 405


# Each admin function and the handler it hands the request to.
ADMIN_FUNCTIONS = {
    "admin_save_room": "admin.rooms.save_room",
    "admin_set_availability": "admin.availability.set_availability",
    "admin_send_message": "admin.staff_message.send_staff_message",
    "admin_hand_back": "admin.hand_back.hand_back",
}


def test_admin_functions_are_exported():
    assert sorted(n for n in main.__all__ if n.startswith("admin_")) == sorted(ADMIN_FUNCTIONS)


def admin_call(name, headers):
    builder = EnvironBuilder(method="POST", path="/", json={}, headers=headers)
    with patch(ADMIN_FUNCTIONS[name]) as handler, flask.Flask(__name__).request_context(builder.get_environ()):
        status = getattr(main, name)(flask.request).status_code
    handler.assert_not_called()
    return status


@pytest.mark.parametrize("name", ADMIN_FUNCTIONS)
@patch("admin.guard.auth.verify_id_token")
def test_admin_functions_reject_a_non_admin(verify, name):
    verify.return_value = {"uid": "uid-1"}
    assert admin_call(name, {"Authorization": "Bearer good"}) == 403


@pytest.mark.parametrize("name", ADMIN_FUNCTIONS)
def test_admin_functions_reject_a_missing_token(name):
    assert admin_call(name, {}) == 401
