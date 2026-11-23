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


ADMIN_FUNCTIONS = [
    "admin_list_conversations",
    "admin_get_messages",
    "admin_list_rooms",
    "admin_save_room",
    "admin_get_availability",
    "admin_set_availability",
]


@pytest.mark.parametrize("name", ADMIN_FUNCTIONS)
def test_admin_functions_are_exported(name):
    assert name in main.__all__


@pytest.mark.parametrize("name", ADMIN_FUNCTIONS)
@patch("admin.guard.auth.verify_id_token")
def test_admin_functions_reject_a_non_admin(verify, name):
    verify.return_value = {"uid": "uid-1"}
    builder = EnvironBuilder(method="POST", path="/", json={}, headers={"Authorization": "Bearer good"})
    with flask.Flask(__name__).request_context(builder.get_environ()):
        assert getattr(main, name)(flask.request).status_code == 403
