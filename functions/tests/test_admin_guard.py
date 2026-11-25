from unittest.mock import MagicMock, patch

import flask
from firebase_admin.exceptions import FirebaseError
from werkzeug.test import EnvironBuilder

from admin import guard

app = flask.Flask(__name__)


def call(handler, body=None, token="Bearer good", method="POST", raw=None):
    headers = {"Authorization": token} if token else {}
    kwargs = {"data": raw} if raw is not None else {"json": body}
    builder = EnvironBuilder(method=method, path="/", headers=headers, **kwargs)
    with app.request_context(builder.get_environ()):
        return guard.run(flask.request, handler)


@patch("admin.guard.auth.verify_id_token")
def test_missing_or_bad_token_is_401(verify):
    handler = MagicMock()
    assert call(handler, {}, token=None).status_code == 401
    assert call(handler, {}, token="Basic abc").status_code == 401
    for error in [ValueError("bad"), FirebaseError("invalid-argument", "bad")]:
        verify.side_effect = error
        assert call(handler, {}).status_code == 401
    handler.assert_not_called()


@patch("admin.guard.auth.verify_id_token")
def test_valid_non_admin_is_403(verify):
    handler = MagicMock()
    for claims in [{"uid": "u"}, {"uid": "u", "admin": False}]:
        verify.return_value = claims
        assert call(handler, {}).status_code == 403
    handler.assert_not_called()


@patch("admin.guard.auth.verify_id_token")
def test_admin_reaches_handler(verify):
    verify.return_value = {"uid": "u", "admin": True}
    handler = MagicMock(return_value={"ok": 1})
    response = call(handler, {"a": 2})
    handler.assert_called_once_with({"a": 2})
    assert response.status_code == 200
    assert response.get_json() == {"ok": 1}


@patch("admin.guard.auth.verify_id_token")
def test_verify_called_with_check_revoked(verify):
    verify.return_value = {"uid": "u", "admin": True}
    call(MagicMock(return_value={}), {})
    verify.assert_called_once_with("good", check_revoked=True)


@patch("admin.guard.auth.verify_id_token")
def test_method_size_and_body_rules(verify):
    verify.return_value = {"uid": "u", "admin": True}
    handler = MagicMock(return_value={})
    assert call(handler, {}, method="GET").status_code == 405
    assert call(handler, raw="x" * 5000).status_code == 413
    assert call(handler, [1]).status_code == 400
    handler.assert_not_called()
    assert call(handler, raw="").status_code == 200
    handler.assert_called_once_with({})


@patch("admin.guard.auth.verify_id_token")
def test_admin_error_and_crash(verify):
    verify.return_value = {"uid": "u", "admin": True}
    response = call(MagicMock(side_effect=guard.AdminError(409, "x")), {})
    assert response.status_code == 409
    assert response.get_json() == {"error": "x"}
    response = call(MagicMock(side_effect=RuntimeError("secret")), {})
    assert response.status_code == 500
    assert response.get_json() == {"error": "Something went wrong. Please try again."}
