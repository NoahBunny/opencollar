"""Tests for shared/focuslock_http.py — JSONResponseMixin."""

import io
import json

from focuslock_http import JSONResponseMixin


class _FakeHandler(JSONResponseMixin):
    """Minimal stand-in for BaseHTTPRequestHandler to exercise respond_json."""

    def __init__(self):
        self.status_code = None
        self.headers = []  # list of (name, value)
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.status_code = code

    def send_header(self, name, value):
        self.headers.append((name, value))

    def end_headers(self):
        pass


class TestRespondJson:
    def test_status_code_set(self):
        h = _FakeHandler()
        h.respond_json(200, {"ok": True})
        assert h.status_code == 200

    def test_body_is_valid_json(self):
        h = _FakeHandler()
        h.respond_json(200, {"key": "value", "count": 42})
        body = h.wfile.getvalue().decode()
        assert json.loads(body) == {"key": "value", "count": 42}

    def test_content_length_matches_body(self):
        h = _FakeHandler()
        h.respond_json(200, {"x": "hello"})
        body = h.wfile.getvalue()
        headers = dict(h.headers)
        assert int(headers["Content-Length"]) == len(body)

    def test_json_content_type(self):
        h = _FakeHandler()
        h.respond_json(200, {})
        headers = dict(h.headers)
        assert headers["Content-Type"] == "application/json"

    def test_security_headers_always_present(self):
        h = _FakeHandler()
        h.respond_json(200, {})
        names = [n for n, _ in h.headers]
        assert "X-Frame-Options" in names
        assert "X-Content-Type-Options" in names
        headers = dict(h.headers)
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"

    def test_cors_opt_in_only(self):
        h = _FakeHandler()
        h.respond_json(200, {}, cors=False)
        names = [n for n, _ in h.headers]
        assert "Access-Control-Allow-Origin" not in names

    def test_cors_enabled(self):
        h = _FakeHandler()
        h.respond_json(200, {}, cors=True)
        headers = dict(h.headers)
        assert headers["Access-Control-Allow-Origin"] == "*"

    def test_list_payload(self):
        h = _FakeHandler()
        h.respond_json(200, [1, 2, 3])
        assert json.loads(h.wfile.getvalue().decode()) == [1, 2, 3]

    def test_error_status(self):
        h = _FakeHandler()
        h.respond_json(403, {"error": "forbidden"})
        assert h.status_code == 403
        assert json.loads(h.wfile.getvalue().decode()) == {"error": "forbidden"}
