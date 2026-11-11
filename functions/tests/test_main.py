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
